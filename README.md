# Cybera Designate Sink

## Purpose

Provide us the ability to auto create A, AAAA, and PTR records for instances and floating IPs as they are created/assigned or deleted/unassigned.

### v6handler

The `nova_fixed_v6` handler will create an AAAA record for the fixed IPv6 address assigned to an instance. The format is the ec2id.ZONE_NAME.
ZONE_NAME will be location.cybera.ca

It will also create a reverse record.

### v4handler

The `nova_floating` handler will create an A record when a floating IP is assigned. The format is *also* ec2id.ZONE_NAME

It will also create a reverse record.

This handler may not be used in the future, as a static predictable entry instead of the instance ec2id hostname sounds preferable
(eg. 111-222-333-444.cloud.cybera.ca)

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
  3. Restart services:`for i in api agent central mdns sink pool-manager; do service designate-$i restart; done`

## Missing Bits

* Easier testing (cli tool)

## Thanks

Thanks to the Designate team, the Time Warner Cable (now Charter Communications) team that made the [cirrus-designate-sink-handler](https://github.com/twc-openstack/cirrus-designate-sink-handler), and [Wikimedia](https://phabricator.wikimedia.org/diffusion/GSNF/repository/master/) as a starting point.

