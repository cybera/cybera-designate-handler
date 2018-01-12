# v6 handler

from oslo_config import cfg
from oslo_log import log as logging

from designate.objects import Record
from designate.notification_handler.base import BaseAddressHandler
from designate.context import DesignateContext

from keystoneauth1.identity import v3
from keystoneauth1 import session
from novaclient.v2 import client as nova_c

import ipaddress

LOG = logging.getLogger(__name__)

cfg.CONF.register_group(cfg.OptGroup(
    name='handler:nova_fixed_v6',
    title="Configuration for Nova notification handler for v6"
))

cfg.CONF.register_opts([
    cfg.ListOpt('notification-topics', default=['notifications']),
    cfg.StrOpt('control-exchange', default='nova'),
    cfg.StrOpt('zone-id'),
    cfg.StrOpt('reverse-zone-id'),
    cfg.StrOpt('auth-url'),
    cfg.StrOpt('admin_user'),
    cfg.StrOpt('admin_password'),
    cfg.StrOpt('admin_tenant_name'),
], group='handler:nova_fixed_v6')

class NovaFixedV6Handler(BaseAddressHandler):
    """Handler for Nova's notifications"""
    __plugin_name__ = 'nova_fixed_v6'

    def get_exchange_topics(self):
        exchange = cfg.CONF[self.name].control_exchange
        topics = [topic for topic in cfg.CONF[self.name].notification_topics]

        return (exchange, topics)

    def get_event_types(self):
        return [
            'compute.instance.create.end',
            'compute.instance.delete.start',
        ]

    def process_notification(self, context, event_type, payload):
        LOG.debug('NovaFixedV6Handler: Event type received %s', event_type)
        LOG.debug('NovaFixedV6Handler: Event body received %s', payload)
        zone = self.get_zone(cfg.CONF[self.name].zone_id)
        reverse_zone = self.get_zone(cfg.CONF[self.name].reverse_zone_id)

        domain_id = zone['id']
        reverse_domain_id = reverse_zone['id']

        if event_type == 'compute.instance.create.end':
            # Need admin context to get the ec2id. Otherwise using the normal context would have worked.
            username = cfg.CONF[self.name].admin_user
            password = cfg.CONF[self.name].admin_password
            tenant_name = cfg.CONF[self.name].admin_tenant_name
            auth_url = cfg.CONF[self.name].auth_url
            auth = v3.Password(username=username, password=password,
                               project_name=tenant_name, project_domain_name='default',
                               user_domain_name='default', auth_url=auth_url)
            sess = session.Session(auth=auth)
            nvc = nova_c.Client(2.1, session=sess)

            instance = nvc.servers.get(payload['instance_id'])

            # Determine the hostname
            ec2id = getattr(instance, 'OS-EXT-SRV-ATTR:instance_name')
            ec2id = ec2id.split('-', 1)[1].lstrip('0')
            hostname = '%s.%s' % (ec2id, zone['name'])

            LOG.debug('NovaFixedV6Handler creating AAAA record (%s) for - %s', hostname, payload['instance_id'])
            # Become Designate Admin to manage records
            context = DesignateContext.get_admin_context(all_tenants=True)

            # 1 recordset of an A and AAAA record

            for fixed_ip in payload['fixed_ips']:
                # Don't create an A record for the private address.
                if fixed_ip['version'] == 4:
                    continue

                record_type = 'AAAA'

                recordset_values = {
                    'zone_id': domain_id,
                    'name': hostname,
                    'type': record_type
                }

                recordset = self._find_or_create_recordset(context, **recordset_values)

                record_values = {
                    'data': fixed_ip['address'],
                    'managed': True,
                    'managed_plugin_name': self.get_plugin_name(),
                    'managed_plugin_type': self.get_plugin_type(),
                    'managed_resource_type': 'instance',
                    'managed_resource_id': payload['instance_id']
                }

                LOG.debug('Creating record in %s / %s with values %r' %
                          (domain_id, recordset['id'], record_values))
                self.central_api.create_record(context,
                                           domain_id,
                                           recordset['id'],
                                           Record(**record_values))

                # Create PTR
                record_type = 'PTR'

                # Calculate reverse address
                v6address = ipaddress.ip_address(fixed_ip['address'])
                reverse_address = v6address.reverse_pointer + '.'

                recordset_values = {
                    'zone_id' : reverse_domain_id,
                    'name' : reverse_address,
                    'type' : record_type
                }

                reverse_recordset = self._find_or_create_recordset(context, **recordset_values)

                record_values = {
                    'data': hostname,
                    'managed': True,
                    'managed_plugin_name': self.get_plugin_name(),
                    'managed_plugin_type': self.get_plugin_type(),
                    'managed_resource_type': 'instance',
                    'managed_resource_id': payload['instance_id']
                }

                LOG.debug('NovaFixedV6Handler Creating record in %s / %s with values %r' %
                          (reverse_domain_id, reverse_recordset['id'], record_values))
                self.central_api.create_record(context,
                                           reverse_domain_id,
                                           reverse_recordset['id'],
                                           Record(**record_values))

                nvc.servers.set_meta_item(instance, 'dns', hostname[:-1])

        elif event_type == 'compute.instance.delete.start':
            # Nova Delete Event does not include fixed_ips. Hence why we had the instance ID in the records.
            LOG.debug('NovaFixedV6Handler delete A and AAAA record for - %s', payload['instance_id'])

            self._delete(zone_id=domain_id,
                    resource_id=payload['instance_id'],
                    resource_type='instance')
            self._delete(zone_id=reverse_domain_id,
                    resource_id=payload['instance_id'],
                    resource_type='instance')

            # search for and delete floating IPs
            elevated_context = DesignateContext.get_admin_context(
                all_tenants=True, edit_managed_records=True)

            criterion = {
                'managed': True,
                'managed_plugin_name': 'neutron_floating',
                'managed_resource_type': 'instance',
                'managed_extra': 'instance:%s' % (payload['instance_id']),
            }
            records = self.central_api.find_records(elevated_context, criterion)
            LOG.debug('Found %d floating ip records to delete for %s' % (len(records), payload['instance_id']))
            for record in records:
                zones = self.central_api.find_zones(elevated_context)
                for zone in zones:
                    try:
                        recordset = self.central_api.get_recordset(elevated_context, zone['id'], record['recordset_id'])
                        LOG.debug('Deleting record %s from %s / %s' % (record['id'], zone['id'], record['recordset_id']))
                        #self.central_api.delete_record(elevated_context, zone['id'], record['recordset_id'], record['id'])
                        self.central_api.delete_recordset(elevated_context, zone['id'], record['recordset_id'])
                    except:
                        pass
