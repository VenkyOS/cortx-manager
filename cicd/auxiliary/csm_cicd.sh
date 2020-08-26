#!/bin/bash

# CORTX-CSM: CORTX Management web and CLI interface.
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.

set -x

RPM_PATH=$1
CSM_REPO_PATH=$2
CSM_PATH=$3


yum install -y $RPM_PATH/*.rpm

# Copy certificates
mkdir -p /etc/ssl/stx/ /etc/cortx/ha/
cp -f $CSM_REPO_PATH/jenkins/cicd/stx.pem /etc/ssl/stx/
#cp -f $CSM_REPO_PATH/jenkins/cicd/etc/database.json /etc/cortx/ha/

groupadd haclient

mkdir -p /opt/seagate/cortx/provisioner/generated_configs/healthmap/
cp -f $CSM_REPO_PATH/jenkins/cicd/etc/ees-schema.json /opt/seagate/cortx/provisioner/generated_configs/healthmap/
chmod 777 /opt/seagate/cortx/provisioner/generated_configs/healthmap/ees-schema.json

python3 -c "import provisioner; print(provisioner.__file__)"
python3 -c "import sys; print(sys.path)"
yum remove salt* -y
pip3 uninstall -y salt

csm_setup post_install
csm_setup config --debug
csm_setup init

#su -c "/usr/bin/csm_agent --debug &" csm
/usr/bin/csm_agent --debug &
# TODO: Run web as csm user after not path issue is resolved
node $CSM_PATH/web/web-dist/server.js &


# TODO: Uncomment when container able to start systemd service
#systemctl restart csm_agent
#systemctl restart csm_web


#systemctl status csm_agent
#systemctl status csm_web

[ -f /etc/var/log/seagate/csm/csm_agent.log ] && {
    cat /etc/var/log/seagate/csm/csm_agent.log
} || {
    echo "Log init failed"
}

sleep 5s
mkdir -p /tmp


/usr/bin/csm_test -t $CSM_PATH/test/plans/cicd.pln -f $CSM_PATH/test/test_data/args.yaml -o /tmp/result.txt