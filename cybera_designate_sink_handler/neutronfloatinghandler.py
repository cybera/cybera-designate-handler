# v4 handler

from oslo_config import cfg
from oslo_log import log as logging

from designate.objects import Record
from designate.notification_handler.base import BaseAddressHandler
from designate.context import DesignateContext
from designate.central import rpcapi as central_api

from keystoneauth1.identity import v3
from keystoneauth1 import session
from novaclient import client as nova_c
from designateclient.v2 import client as designate_c

from cybera_designate_sink_handler.ip_handler import IPHandler

import ipaddress

LOG = logging.getLogger(__name__)

cfg.CONF.register_group(cfg.OptGroup(
    name='handler:neutron_floating',
    title="Configuration for Neutron notification handler for floating v4 IPs"
))

cfg.CONF.register_opts([
    cfg.ListOpt('notification-topics', default=['notifications']),
    cfg.StrOpt('control-exchange', default='nova'),
    cfg.StrOpt('zone-id'),
    cfg.StrOpt('zone-owner-tenant-id'),
    cfg.StrOpt('auth-url'),
    cfg.StrOpt('admin-user'),
    cfg.StrOpt('admin-password'),
    cfg.StrOpt('admin-tenant-name'),
    cfg.StrOpt('floating_ip_prefix_id'),
    cfg.StrOpt('netbox_api_key')
], group='handler:neutron_floating')


class NeutronFloatingHandler(BaseAddressHandler):
    """Handler for Neutron's notifications"""
    __plugin_name__ = 'neutron_floating'

    def get_exchange_topics(self):
        exchange = cfg.CONF[self.name].control_exchange
        topics = [topic for topic in cfg.CONF[self.name].notification_topics]

        return (exchange, topics)

    def get_event_types(self):
        return [
            'floatingip.update.end',
            'floatingip.delete.start',
        ]

    def process_notification(self, context, event_type, payload):
        LOG.debug('NeutronFloatingHandler: Event type received: %s', event_type)
        LOG.debug('NeutronFloatingHandler: Event body received: %s', payload)
        zone_id = cfg.CONF[self.name].zone_id
        zone = self.get_zone(zone_id)

        # Get a list all all zones owned by the zone tenant owner.
        # This is so we can find the reverse DNS zone.
        elevated_context = DesignateContext.get_admin_context(
            all_tenants=True, edit_managed_records=True)

        criterion = {
            "tenant_id": cfg.CONF[self.name].zone_owner_tenant_id,
        }

        zones = self.central_api.find_zones(elevated_context, criterion)

        # Keystone auth
        username = cfg.CONF[self.name].admin_user
        password = cfg.CONF[self.name].admin_password
        tenant_name = cfg.CONF[self.name].admin_tenant_name
        auth_url = cfg.CONF[self.name].auth_url
        auth = v3.Password(username=username, password=password,
                           project_name=tenant_name, project_domain_name='default',
                           user_domain_name='default', auth_url=auth_url)
        sess = session.Session(auth=auth)
        nvc = nova_c.Client(2.1, session=sess)

        # IP address
        floatingip = payload['floatingip']['floating_ip_address']
        v4address = ipaddress.ip_address(floatingip)

        # DNS name
        search_opts = {
            'ip': payload['floatingip']['fixed_ip_address'],
            'status': 'ACTIVE',
            'all_tenants': True,
            'tenant_id': payload['floatingip']['tenant_id'],
        }
        instances = nvc.servers.list(detailed=True, search_opts=search_opts)
        instance = instances[0]
        ec2id = getattr(instance, 'OS-EXT-SRV-ATTR:instance_name')
        ec2id = ec2id.split('-', 1)[1].lstrip('0')

        # For netbox updating
        ip_handler_address = v4address
        ip_handler_dns = '%s.%s' % (ec2id, zone['name'])
        ip_handler_project = context['project_name']

        prefix_id = 71  # int(cfg.CONF[self.name].floating_ip_prefix_id)
        netbox_api_key = str(cfg.CONF[self.name].netbox_api_key)

        try:
            ip_handler = IPHandler(
                ip_ver=4,
                netbox_api_key=netbox_api_key,
                floating_ip_prefix_id=prefix_id
            )
        except Exception as e:
            LOG.warning("ip handler was not initialized {0}".format(e))

        if event_type.startswith('floatingip.delete'):
            self._delete(zone_id=zone_id,
                         resource_id=payload['floatingip_id'],
                         resource_type='instance')

            try:
                LOG.debug(
                    'Fetching netbox IP entry for %s' %
                    (ip_handler_address)
                )
                nb_ip = ip_handler.get_ip(str(ip_handler_address))

                LOG.debug(
                    'Unassigning IP address in netbox - IP: "%s" DNS: "%s" PROJECT: "%s"' %
                    (ip_handler_address, ip_handler_dns, ip_handler_project)
                )

                ip_handler.unassign_ip(nb_ip)

            except Exception as e:
                LOG.warning(
                    "v4 address unassignment in netbox failed: {0}".format(e))

        elif event_type.startswith('floatingip.update'):
            # Calculate Reverse Address
            reverse_address = v4address.reverse_pointer + '.'
            reverse_network = '.'.join(reverse_address.split('.')[1:])
            reverse_id = None

            # Loop through all zones and see if one matches the reverse zone
            reverse_id = None
            for i in zones:
                if i.name == reverse_network:
                    reverse_id = i.id

            if payload['floatingip']['fixed_ip_address']:
                # Search for an instance with the matching fixed ip
                search_opts = {
                    'ip': payload['floatingip']['fixed_ip_address'],
                    'status': 'ACTIVE',
                    'all_tenants': True,
                    'tenant_id': payload['floatingip']['tenant_id'],
                }
                instances = nvc.servers.list(
                    detailed=True, search_opts=search_opts)

                if len(instances) == 1:
                    instance = instances[0]
                    # Get the ec2 id of the instance and build the hostname from it
                    ec2id = getattr(instance, 'OS-EXT-SRV-ATTR:instance_name')
                    ec2id = ec2id.split('-', 1)[1].lstrip('0')
                    hostname = '%s.%s' % (ec2id, zone['name'])

                    # create a recordset
                    record_type = 'A'
                    recordset_values = {
                        'zone_id': zone_id,
                        'name': hostname,
                        'type': record_type
                    }

                    record_values = {
                        'data': floatingip,
                        'managed': True,
                        'managed_plugin_name': self.get_plugin_name(),
                        'managed_plugin_type': self.get_plugin_type(),
                        'managed_resource_type': 'instance',
                        'managed_resource_id': payload['floatingip']['id'],
                        'managed_extra': 'instance:%s' % (getattr(instance, 'id')),
                    }

                    LOG.debug('NeutronFloatingHandler Creating record in %s / %s with values %r' %
                              (zone_id, hostname, record_values))
                    recordset = self._create_or_update_recordset(
                        elevated_context,
                        [Record(**record_values)],
                        **recordset_values)

                    # create a reverse recordset
                    record_type = 'PTR'

                    if reverse_id == None:
                        LOG.debug('UNABLE TO DETERMINE REVERSE ZONE: %s',
                                  payload['floatingip'])

                    else:
                        recordset_values = {
                            'zone_id': reverse_id,
                            'name': reverse_address,
                            'type': record_type
                        }

                        record_values = {
                            'data': hostname,
                            'managed': True,
                            'managed_plugin_name': self.get_plugin_name(),
                            'managed_plugin_type': self.get_plugin_type(),
                            'managed_resource_type': 'instance',
                            'managed_resource_id': payload['floatingip']['id'],
                            'managed_extra': 'instance:%s' % (getattr(instance, 'id')),
                        }

                        LOG.debug('NeutronFloatingHandler Creating PTR record in %s / %s with values %r' %
                                  (reverse_id, reverse_address, record_values))

                        recordset = self._create_or_update_recordset(
                            elevated_context,
                            [Record(**record_values)],
                            **recordset_values)

                        try:
                            # Get netbox ip object, will create one if it's not found
                            LOG.debug(
                                'Fetching netbox IP entry for %s' %
                                (ip_handler_address)
                            )
                            nb_ip = ip_handler.get_ip(str(ip_handler_address))

                            LOG.debug(
                                'Updating netbox with IP address assignment - IP: "%s" DNS: "%s" PROJECT: "%s"' %
                                (ip_handler_address,
                                 ip_handler_dns, ip_handler_project)
                            )
                            ip_handler.assign_ip(
                                nb_ip, ip_handler_dns, ip_handler_project)

                        except Exception as e:
                            LOG.warning(
                                "v4 address update for netbox failed: %s" % e)
                            # LOG.warning("v4 address update in netbox failed: {0}".format(payload.__dict__))

            else:
                LOG.debug('Deleting records for %s / %s' %
                          (zone_id, payload['floatingip']['id']))
                self._delete(
                    zone_id=zone_id, resource_id=payload['floatingip']['id'], resource_type='instance')

                try:
                    LOG.debug(
                        'Fetching netbox IP entry for %s' %
                        (ip_handler_address)
                    )
                    nb_ip = ip_handler.get_ip(str(ip_handler_address))

                    LOG.debug(
                        'Unassigning IP address in netbox - IP: "%s" DNS: "%s" PROJECT: "%s"' %
                        (ip_handler_address, ip_handler_dns, ip_handler_project)
                    )

                    ip_handler.unassign_ip(nb_ip)

                except Exception as e:
                    LOG.warning(
                        "v4 address unassignment in netbox failed: {0}".format(e))

                if reverse_id == None:
                    LOG.debug('UNABLE TO DETERMINE REVERSE ZONE: %s',
                              payload['floatingip'])
                else:
                    LOG.debug('Deleting records for %s / %s' %
                              (reverse_id, payload['floatingip']['id']))
                    self._delete(zone_id=reverse_id,
                                 resource_id=payload['floatingip']['id'],
                                 resource_type='instance')
