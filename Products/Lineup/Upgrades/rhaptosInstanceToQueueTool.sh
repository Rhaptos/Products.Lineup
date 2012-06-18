#!/bin/bash

# Usage documentation
usage() {
  cmd=`basename $0`
  echo \
"Modifies an existing Rhaptos instance to act as a QueueTool worker instance.
Basically, it changes zope.conf .

Usage:
  $cmd [options] [instance name]

Instance name defaults to "cnx".

Options:
  -f <directory>          Folder to hold the instance. Default: /opt/instances
  -d                      This is being run on a dev instance. Don't disable http-server
"
  exit 1
}

IS_DEV_INSTANCE=''

if [[ $1 == "-h"  || $1  == "--help" ]]; then usage; fi

while getopts  "hf:sd" opt
do
  case "$opt" in
    f)   DIR=$OPTARG;;
    d)   IS_DEV_INSTANCE='true';;
    h)   usage
  esac
done
shift $((OPTIND-1))

INSTANCEHOLD=${DIR:-/opt/instances}
INSTANCENAME=${1:-cnx}
INSTANCEPATH=$INSTANCEHOLD/$INSTANCENAME


# echo $user
# echo $INSTANCEHOLD
# echo $INSTANCENAME
# echo $INSTANCEPATH
# exit 1

#Check if the instance exists
[ ! -f $INSTANCEPATH/etc/zope.conf ] && echo "ERROR: No instance found at $INSTANCEPATH" && exit 2


echo "Checking if ClockServer Product exists"
[ ! -d $INSTANCEPATH/Products/ClockServer ] && echo "ERROR: Products.ClockServer does not exist. Please download it from http://www.contentmanagementsoftware.info/zope/ClockServer" && exit 3
echo "  It does. Continuing..."


echo "Adding ClockServer info to zope.conf"
CLOCKSERVER_CONFIGURED=`grep "%import Products.ClockServer" $INSTANCEPATH/etc/zope.conf`

if [[ $CLOCKSERVER_CONFIGURED == '' ]]; then
  echo \
"%import Products.ClockServer
<clock-server>
  method /plone/queue_tool/clockTick
  period 3
  user
  password
</clock-server>" >> $INSTANCEPATH/etc/zope.conf

else
  echo "  WARNING: It appears the ClockServer is already configured. Continuing..."
fi

echo "Done."
