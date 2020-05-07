# Docker Setup for GNUHealth Server & Postgres database

This Docker Setup provides a quick and easy way to get a
[GNU Health](http://www.gnuhealth.org/index.html) server
up and running with only a few steps.
Furthermore this docker setup may be used as a base to create your
own docker based environment for production use.

This build is based on the official docker images

* [amazonlinux base image](https://registry.hub.docker.com/_/amazonlinux)
* [Postgres base image](https://registry.hub.docker.com/_/postgres/)

Using

* [docker-compose](https://docs.docker.com/compose/)

## Installation

### Prerequisites

## Usage

Create a working directory

    mkdir ~/gnuhealth
    cd gnuhealth

Run

    docker-compose up

and get yourself a cup of coffee...

The first setup will take some time for

* downloading the images
* importing the GNU Health database

Subsequent calls to `docker-compose up` will run much faster.
Stop the servers with Ctrl+C.

    Server: <your_machine>:8000
    User name: admin_es
    Password: gnusolidario

## Authors and Credits

This setup was made by [Bayron Barahona](https://github.com/Bhona) in the hope, that it may be useful
for the GNU Health users. Thanks to the [GNU Health project](http://health.gnu.org/)
for providing this free Health and Hospital Information System.

Parts of this setup were adopted from

* [postgres](https://github.com/docker-library/postgres/) by [Docker Official Image Packaging for Postgres](https://github.com/docker-library/postgres/).


## Support

For any questions about this image and docker setup you may contact us at [support](mailto:bayron.barahona@gmail.com).