Name:           genesis-PRODUCT
Version:        1.0.0
Release:        1%{?dist}
Summary:        genesis-rpm 
BuildArch:      noarch

License:        (c) genesis.global
Group:          Genesis Platform
URL:            https://genesis.global/
Source0:        server-%{version}.tar.gz
Source1:        web-%{version}.tar.gz

Requires:       %{name} = %{version}
Requires:       /bin/sh
Requires:       rpmlib(CompressedFileNames) <= 3.0.4-1
Requires:       rpmlib(FileDigests) <= 4.6.0-1
Requires:       rpmlib(PayloadFilesHavePrefix) <= 4.0-1

%description
%files
%dir %attr(1777, root, root) "/tmp"
%attr(1777, root, root) "/tmp/server-%{version}.tar.gz"
%attr(1777, root, root) "/tmp/web-%{version}.tar.gz"

%install
cd $HOME
mkdir -p rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64
ls -al rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64
pwd
mkdir rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64/tmp
ls -al rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64
cp rpmbuild/SOURCES/server-%{version}.tar.gz rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64/tmp/
cp rpmbuild/SOURCES/web-%{version}.tar.gz rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64/tmp/
cd rpmbuild/BUILDROOT/genesis-tam-1.0.0-1.amzn2.x86_64/tmp/
ls
pwd

%prep
%setup -c

%post -p /bin/sh
#!/usr/bin/env bash

exec 1>/proc/$PPID/fd/1
exec 2>/proc/$PPID/fd/2

## Set the product user and group if specified

genesis_user="genesisUser"
genesis_grp="genesisUser"
root_dir="data"
server_dir=$(date +%Y%m%d-%H%M)

if [ ! "$(test -d /var/log/genesis_service  && echo 1 || echo 0)" -eq 1  ]
then
    sudo install -d /var/log/genesis_service -o $genesis_user -m 750
else 
    echo "/var/log/genesis_service is already present"
fi

echo "Default genesis_user is: $genesis_user"
echo "Default user group is $genesis_grp"
echo "Default installation directory is: $genesis_user"


if [ "$(test -f /tmp/genesis_install.conf && echo 1 || echo 0)" -eq 1 ] && [ "$(grep genesis_user -ic /tmp/genesis_install.conf)" -gt 0 ]
then
    echo "New genesis_user provided in the /tmp/genesis_install.conf is: $genesis_user"
    genesis_user=$(grep genesis_user /tmp/genesis_install.conf | cut -d '=' -f 2)
fi

if [ "$(test -f /tmp/genesis_install.conf && echo 1 || echo 0)" -eq 1 ] && [ "$(grep genesis_grp -ic /tmp/genesis_install.conf)" -gt 0 ]
then
    echo "New genesis user group provided in the /tmp/genesis_install.conf is: $genesis_grp"
    genesis_grp=$(grep genesis_grp /tmp/genesis_install.conf | cut -d '=' -f 2)
fi

if [ "$(test -f /tmp/genesis_install.conf && echo 1 || echo 0)" -eq 1 ] && [ "$(grep root_dir -ic /tmp/genesis_install.conf)" -gt 0 ]
then
    echo "New installation directory provided in the /tmp/genesis_install.conf is: $root_dir"
    root_dir=$(grep root_dir /tmp/genesis_install.conf | cut -d '=' -f 2)
fi

#Create genesis user if doesn't exist
echo "Create $genesis_user if doesn't exist"
if [ "$(test -d /home/"$genesis_user" && echo 1 || echo 0)" -eq 0 ]
then
    echo "Creating $genesis_user .... "
    sudo adduser -m "$genesis_user"
    echo "$genesis_user""          soft     nproc          16384" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          hard     nproc          16384" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          soft     nofile         65536" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          hard     nofile         65536" | sudo tee -a /etc/security/limits.conf
else
    echo "User present. Carrying on ..  "
fi

# Backup keys to /tmp/keys/
if [[ -d /home/$genesis_user/run/runtime/keys ]]
then
    echo "Directory keys exists in runtime." 
    echo "Moving keys to /tmp/"
    cp -r /home/"$genesis_user"/run/runtime/keys /tmp/
fi

# kill server
echo "Kill servers..."
if [[ "$(grep GENESIS_HOME -ic /home/"$genesis_user"/.bashrc)" -gt 0 && -f run/genesis ]]
then
    echo "Stopping the genesis platform"
    runuser -l "$genesis_user" -c 'echo y | killServer --all'
    runuser -l "$genesis_user" -c 'killProcess GENESIS_CLUSTER'   
fi

#Backup the database according to the config
echo "Only backup db is db_backup is mentioned in /tmp/genesis_install.conf"
if [ "$(test -f /tmp/genesis_install.conf && echo 1 || echo 0)" -eq 1 ] && [ "$(grep db_backup -ic /tmp/genesis_install.conf)" -gt 0 ] && [ "$(grep db_backup /tmp/genesis_install.conf | cut -d '=' -f 2)" = 'false' ]
then
    echo "db_backup is false in /tmp/genesis_install.conf or /tmp/genesis_install.conf is not defined"
else
    echo "db_backup is true in /tmp/genesis_install.conf"
    mkdir -p /"$root_dir"/"$genesis_user"/dbbackup/"$server_dir"
    chown -R "$genesis_user"."$genesis_grp" /"$root_dir"/"$genesis_user"/dbbackup/
    runuser -l "$genesis_user" -c "cd /$root_dir/$genesis_user/dbbackup/$server_dir;JvmRun global.genesis.environment.scripts.DumpTable --all;gzip *"
fi

# Create install log
echo "Create install log.."
LOG=/home/$genesis_user/genesisInstall_$(date +%Y-%m-%d-%H-%M).log
echo "Genesis $genesis_user Install started at $(date)" >> "$LOG"
echo "Genesis $genesis_user Install started at $(date)" 
chown "$genesis_user"."$genesis_grp" "$LOG"

# Extract directory structure
echo "extract the servr directory structure"
mkdir -p /"$root_dir"/"$genesis_user"/server/"$server_dir"/run
cd /"$root_dir"/"$genesis_user"/server/"$server_dir"/run/ || exit 
tar xzf /tmp/server-%{version}.tar.gz &> /dev/null
rm -f /tmp/server-%{version}.tar.gz

#copy runtime
echo "Backup and copy the existing runtime from previous installations, if any...."
if [ "$(test -d /home/"$genesis_user"/run/runtime && echo 1 || echo 0)" -eq 1 ]
then
    cp -R /home/"$genesis_user"/run/runtime /"$root_dir"/"$genesis_user"/server/"$server_dir"/run/
fi

echo "Unlink previous run and link it to the run dir of the current installation"
unlink /home/"$genesis_user"/run
ln -s /"$root_dir"/"$genesis_user"/server/"$server_dir"/run/ /home/"$genesis_user"/run
chown "$genesis_user"."$genesis_grp" /home/"$genesis_user"/run

#Copy web if exists
echo "Check if web is being deployed ..."
if [ -f /tmp/genesis_product_name_web.tar.gz ]
then
    echo "Web is being deployed too ... "
    cd /"$root_dir"/"$genesis_user"/ || exit
    mkdir web-"$server_dir"
    cd web-"$server_dir || exit" || exit
    #check if the web app is not to be served from root
    echo "Check if new web isntallation dir has been provided"
    if [ "$(test -f /tmp/genesis_install.conf && echo 1 || echo 0)" -eq 1 ] && [ "$(grep web_path -ic /tmp/genesis_install.conf)" -gt 0 ]
    then
        web_path=$(grep web_path /tmp/genesis_install.conf | cut -d '=' -f 2)
        mkdir "$web_path"
        cd "$web_path || exit" || exit
        echo "new web isntallation dir is : $web_path"
    fi
    echo "Unlink old web installation and point it to the new web folder"
    tar xzf  /tmp/genesis_product_name_web.tar.gz &> /dev/null
    unlink /"$root_dir"/"$genesis_user"/web
    ln -s /"$root_dir"/"$genesis_user"/web-"$server_dir"/ /"$root_dir"/"$genesis_user"/web
    rm -f /tmp/genesis_product_name_web.tar.gz
fi

chown -R "$genesis_user"."$genesis_grp" /"$root_dir"/"$genesis_user"

# Set up bashrc
echo "Setting up bashrc for the $genesis_user if its not present"
if [ "$(grep GENESIS_HOME -ic /home/"$genesis_user"/.bashrc)" -eq 0 ]
then
    {
        echo "export GENESIS_HOME=\$HOME/run/" 
        echo "[ -f \$GENESIS_HOME/genesis/util/setup.sh ] && source \$GENESIS_HOME/genesis/util/setup.sh" 
        echo "export GROOVY_HOME=/opt/groovy" 
        echo "PATH=\$GROOVY_HOME/bin:\$PATH"
    } >> /home/"$genesis_user"/.bashrc
    echo "bashrc setup complete..."
fi


if [[ ($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 0) || (($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 1) && ($(grep run_exec -ic /tmp/genesis_install.conf) -eq 0) || (($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 1) && ($(grep run_exec -ic /tmp/genesis_install.conf) -gt 0) && ($(sed -n 's/^run_exec=\(.*\)/\1/p' < /tmp/genesis_install.conf) != "false"))) ]]
then
  echo "run_exec has been defined in /tmp/genesis_install.conf as: $(sed -n 's/^run_exec=\(.*\)/\1/p' < /tmp/genesis_install.conf)"
  
  # Run command to clear cache
  echo "Check if site-specific scripts folder exits.."
  runuser -l "$genesis_user" -c "ls -l /home/$genesis_user/run//site-specific/scripts/"
  echo "Running Genesis cache clear command"
  runuser -l "$genesis_user" -c "JvmRun -modules=genesis-environment global.genesis.environment.scripts.ClearCodegenCache"
  
  # Run genesisInstall
  echo "Running Genesis Install script"
  runuser -l "$genesis_user" -c 'genesisInstall'

  # Run Remap
  echo "Running Remap"
  runuser -l "$genesis_user" -c 'echo y | remap --commit --force'
else
  echo "/tmp/genesis_install is absent or run_exec has been defined in /tmp/genesis_install.conf as: $(sed -n 's/^run_exec=\(.*\)/\1/p' < /tmp/genesis_install.conf)"
  echo "genesisInstall and remap will not be run"
fi

# Restore backups
if [[ -d /tmp/keys ]] 
then
    echo "keys do not exist in runtime. Restoring backup"
    cp -r /tmp/keys /home/"$genesis_user"/run/runtime/
    echo "Backup keys restored, cleaning up"
    rm -rf /tmp/keys/
    chown -R "$genesis_user":"$genesis_grp" /home/axes/run/runtime/keys
fi

if [[ ($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 0) || (($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 1) && ($(grep run_exec -ic /tmp/genesis_install.conf) -eq 0) || (($(test -f /tmp/genesis_install.conf && echo 1 || echo 0) -eq 1) && ($(grep run_exec -ic /tmp/genesis_install.conf) -gt 0) && ($(sed -n 's/^run_exec=\(.*\)/\1/p' < /tmp/genesis_install.conf) != "false"))) ]]
then
    #Start the server
	echo "/tmp/genesis_install.conf file absent or run_exec not defined .... Starting servers ...."
    runuser -l "$genesis_user" -c 'startServer'
fi
echo "Genesis $genesis_user Install finished at $(date)" >> "$LOG"
echo "Install.sh has completed ..."


%changelog
