import logging

from time import sleep

import pynetbox
from pynetbox.core.response import RecordSet

LOG = logging.getLogger(__name__)


class IPHandler(object):
    def __init__(self, ip_ver, netbox_api_key, floating_ip_prefix_id):
        self.ip_ver = ip_ver
        self.nb = pynetbox.api("https://netbox.cybera.ca/", netbox_api_key)

        self.prefix = 71
        try:
            self.prefix = dict(self.nb.ipam.prefixes.get(floating_ip_prefix_id))['id']
        except:
            pass

    def create_ip(self, address):
        try:
            self.nb.ipam.ip_addresses.create(address=address)
            created_ip = self.nb.ipam.ip_addresses.filter(address=address).__iter__().__next__()

            if created_ip:
                return created_ip
        except Exception as e:
            LOG.warning("v6 Address not created: {0}".format(e))

    def get_ip(self, address):
        address = str(address)

	ip = self.nb.ipam.ip_addresses.filter(address=address, prefix=self.prefix)
	it = ip.__iter__()
        if self.ip_ver == 6:
            return self.create_ip(address)
	try:
	    return it.__next__()
	except StopIteration:
            LOG.warning("get_ip() failed:  TYPE: {1}".format(dir(ip.__iter__())))
            return False

    def unassign_ip(self, ip):
        if self.ip_ver == 4:
            try:
                ip.update({'description': 'Floating IP'})
            except Exception as e:
                LOG.warning("Couldn't run unassign method: {0}".format(e))

        elif self.ip_ver == 6:
            ip.delete()

    def assign_ip(self, ip, dns, project):

        try:
            description = "{0} ({1})".format(project, dns)
            ip.update({'description': description})
        except Exception as e:
            LOG.warning("Couldn't run assign method: {0}".format(e))

