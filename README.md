# Cybera Designate Sink

## Purpose

Provide us the ability to auto create A, AAAA, and PTR records for instances and floating IPs as they are created/assigned or deleted/unassigned.

## Handlers

### v6handler

The `nova_fixed_v6` handler will create an AAAA record for the fixed IPv6 address assigned to an instance. The format is the ec2id.ZONE_NAME.

ZONE_NAME will be location.cybera.ca

It will also create a reverse record.

Usage:

Add the following to `/etc/designate/designate.conf`:

```
[handler:nova_fixed_v6]
notification_topics = notifications
zone_id = a95f4808-7f9c-4d10-99c7-a83966547a69
reverse_zone_id = 555c21cd-de1b-46a2-82ee-5955deefa5e2
control_exchange = neutron
auth_url = http://127.0.0.1:5000/v2.0
admin_user = designate
admin_password = password
admin_tenant_name = services
```

### v4handler (deprecated)

The `nova_floating` handler will create an A record when a floating IP is assigned. The format is *also* ec2id.ZONE_NAME

It will also create a reverse record.

### neutronfloatinghandler

The `neutron_floating` handler is identical to the nova-network based v4handler described above but instead reacts and uses Neutron events instead of Nova Network events.

Usage:

Add the following to `/etc/designate/designate.conf`:

```
[handler:neutron_floating]
notification_topics = notifications
zone_id = a95f4808-7f9c-4d10-99c7-a83966547a69
control_exchange = neutron
auth_url = http://127.0.0.1:5000/v3
zone_owner_tenant_id = 3637d239c6614fce8768002e124a96db
admin_user = designate
admin_password = password
admin_tenant_name = services
```

### neutronv6handler (not used)

The `neutron_fixed_v6` handler is identical to the v6handler but will check to ensure it only functions on the default public v6 network.

As such it will create an AAAA record for the fixed IPv6 address assigned to an instance. The format is the ec2id.ZONE_NAME.

ZONE_NAME will be location.cybera.ca

It will also create a reverse record.

## Building (Debian)

To build a release you'll need `python-stdeb` installed:

    python setup.py --command-packages=stdeb.command bdist_deb

Then upload the resulting `.deb` file from `deb_dist/` to your preferred storage area for download and installation.

(Thanks Wikimedia instructions)

## Building (RedHat)

While untested with this repository, creating RPMs from python packages is straight forward:

    python setup.py bdist_rpm

## Installation (Debian based installations)

  1. Clone this repository and build the .deb file (See Building section)
  2. Install the .deb file (`dpkg -i blah.deb`)
  3. Restart the sink service: `service designate-sink restart`

## Development

Clone this repository to `/usr/lib/python2.7/dist-packages` (ubuntu) or `/usr/lib/python2.7/site-packages/` (centos) and then run:

    python setup.py develop

## Testing

In one terminal window, run `tail -f /path/to/logfile`.

In another terminal, do:

```shell
$ openstack floating ip create public
$ openstack server floating ip add <name> <floating ip>
$ openstack server floating ip remove <name> <floating ip>
$ openstack floating ip delete <floating ip>
```

After each of the above steps, run:

```shell
$ openstack recordset list <zone id> --all
```

## Missing Bits

* Easier testing (cli tool)

## Thanks

Thanks to the Designate team, the Time Warner Cable (now Charter Communications) team that made the [cirrus-designate-sink-handler](https://github.com/twc-openstack/cirrus-designate-sink-handler), and [Wikimedia](https://phabricator.wikimedia.org/diffusion/GSNF/repository/master/) as a starting point.
