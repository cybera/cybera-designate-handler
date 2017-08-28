# v6 handler

from oslo_config import cfg
from oslo_log import log as logging

from designate.objects import Record
from designate.notification_handler.base import BaseAddressHandler
from designate.context import DesignateContext

from keystoneclient.v2_0 import client as keystone_c
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
        LOG.debug('NovaFixedV6Handler: %s', event_type)
        zone = self.get_zone(cfg.CONF[self.name].zone_id)
        reverse_zone = self.get_zone(cfg.CONF[self.name].reverse_zone_id)

        domain_id = zone['id']
        reverse_domain_id = reverse_zone['id']

        if event_type == 'compute.instance.create.end':
            # Need admin context to get the ec2id. Otherwise using the normal context would have worked.
            kc = keystone_c.Client(username=cfg.CONF[self.name].admin_user,
                    password=cfg.CONF[self.name].admin_password,
                    tenant_name=cfg.CONF[self.name].admin_tenant_name,
                    auth_url = cfg.CONF[self.name].auth_url)

            nova_endpoint = kc.service_catalog.url_for(service_type='compute',
                        endpoint_type='internalURL')

            nvc = nova_c.Client(auth_token=kc.auth_token,
                        tenant_id=kc.auth_tenant_id,
                        bypass_url=nova_endpoint)

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
            LOG.debug('NovaFixedV6Handler delete AAAA record for - %s', payload['instance_id'])

            self._delete(zone_id=domain_id,
                    resource_id=payload['instance_id'],
                    resource_type='instance')
            self._delete(zone_id=reverse_domain_id,
                    resource_id=payload['instance_id'],
                    resource_type='instance')

