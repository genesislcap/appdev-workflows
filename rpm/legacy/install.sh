#!/usr/bin/env bash

exec 1>/proc/$PPID/fd/1
exec 2>/proc/$PPID/fd/2

echo "STARTING" > /tmp/genesis_installation_status

##############################################
####### Functions ############################
##############################################
genesis_is_running() {
    # Get the keyword to search for
    local keyword="global.genesis"

    # Use ps and grep to search for processes
    local count=$(ps aux | grep -c "$keyword")

    # Check the count of matching processes
    if [[ $count -gt 1 ]]; then
        return 0  # true
    else
        return 1  # false
    fi
}

genesis_is_installed() {
    if [ -d /home/"$genesis_user"/run/generated ]; then
        return 0 # true
    else
        return 1 # false
    fi
}

get_override_value() {
    local override_key=$1
    local default_value=$2

    if [ -f /tmp/genesis_install.conf ] && grep --quiet -i "$override_key=.*" /tmp/genesis_install.conf; then
        grep -i "^$override_key=" "/tmp/genesis_install.conf" | sed "s/^$override_key=//"
    else
        echo $default_value
    fi
}

##############################################

## Set the product user and group if specified

genesis_user=$(get_override_value "genesis_user" "genesisUser")
genesis_grp=$(get_override_value "genesis_grp" "genesisUser")
root_dir=$(get_override_value "root_dir" "data")
db_backup=$(get_override_value "db_backup" "true")
run_genesis_install=$(get_override_value "run_genesis_install" "true")
run_genesis_clear_codegen_cache=$(get_override_value "run_genesis_clear_codegen_cache" "true")
run_genesis_remap=$(get_override_value "run_genesis_remap" "true")
start_processes=$(get_override_value "start_processes" "true")
skip_install_hooks=$(get_override_value "skip_install_hooks" "false")
web_path=$(get_override_value "web_path" "/$root_dir/$genesis_user/web")

run_exec=$(get_override_value "run_exec" "true")

if [ $run_exec = "false" ]; then
    run_genesis_install="false"
    run_genesis_remap="false"
    start_processes="false"
fi

server_dir=$(date +%Y%m%d-%H%M)

# Create install log
LOG=/home/$genesis_user/genesisInstall_$server_dir.log
echo "Genesis $genesis_user Install started at $(date)" 2>&1 | tee -a "$LOG"
chown "$genesis_user"."$genesis_grp" "$LOG"
echo "Create install log.."

if [ ! -d /var/log/genesis_service ]; then
    sudo install -d /var/log/genesis_service -o $genesis_user -m 750
else
    echo "/var/log/genesis_service is already present"
fi

echo "genesis_user is: $genesis_user" 2>&1 | tee -a "$LOG"
echo "user group is $genesis_grp" 2>&1 | tee -a "$LOG"
echo "installation directory is: $genesis_user" 2>&1 | tee -a "$LOG"


#Create genesis user if doesn't exist
echo "Create $genesis_user if doesn't exist"
if [ ! -d /home/$genesis_user ]; then
    echo "Creating $genesis_user .... " 2>&1 | tee -a "$LOG"
    sudo adduser -m "$genesis_user"
    echo "$genesis_user""          soft     nproc          16384" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          hard     nproc          16384" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          soft     nofile         65536" | sudo tee -a /etc/security/limits.conf
    echo "$genesis_user""          hard     nofile         65536" | sudo tee -a /etc/security/limits.conf
else
    echo "User present. Carrying on ..  " 2>&1 | tee -a "$LOG"
fi

# Backup keys to /tmp/keys/
if [ -d /home/$genesis_user/run/runtime/keys ]; then
    echo "Directory keys exists in runtime." 2>&1 | tee -a "$LOG"
    echo "Moving keys to /tmp/" 2>&1 | tee -a "$LOG"
    cp -r /home/"$genesis_user"/run/runtime/keys /tmp/
fi

# kill server
if genesis_is_running; then
    echo "Detected Genesis processes running, attempting to kill them..." 2>&1 | tee -a "$LOG"
    if genesis_is_installed; then
        runuser -l "$genesis_user" -c 'echo y | killServer --all'
        runuser -l "$genesis_user" -c 'echo y | killDaemon'
    else
        echo "Genesis doesn't seem to be installed, we will try and stop the processes after the new installation" 2>&1 | tee -a "$LOG"
    fi
fi

#Backup the database according to the config
echo "Backup if Genesis is installed unless told otherwise." 2>&1 | tee -a "$LOG"
if [ "$db_backup" = "true" ] && genesis_is_installed; then
    echo "db_backup is true" 2>&1 | tee -a "$LOG"
    mkdir -p "/$root_dir/$genesis_user/dbbackup/$server_dir" || exit 1
    chown -R "$genesis_user:$genesis_grp" "/$root_dir/$genesis_user/dbbackup/" || exit 1
    runuser -l "$genesis_user" -c "cd /$root_dir/$genesis_user/dbbackup/$server_dir;JvmRun global.genesis.environment.scripts.DumpTable --all;gzip *" || exit 1
else
    echo "db_backup is false in /tmp/genesis_install.conf or /tmp/genesis_install.conf is not defined" 2>&1 | tee -a "$LOG"
fi

# Extract directory structure
echo "extract the servr directory structure" 2>&1 | tee -a "$LOG"
mkdir -p "/$root_dir/$genesis_user/server/$server_dir/run" || exit 1
cd "/$root_dir/$genesis_user/server/$server_dir/run/" || exit 1
tar -xf /tmp/genesis_product_name_package.tar.gz &> /dev/null || exit 1
rm -f /tmp/genesis_product_name_package.tar.gz || exit 1
ls -tp "/$root_dir/$genesis_user/server/" | grep  '/$' | tail -n +5 | xargs -I {} rm -rf -- "/$root_dir/$genesis_user/server/{}"

#copy runtime
echo "Backup and copy the existing runtime from previous installations, if any...." 2>&1 | tee -a "$LOG"
if [ -d "/home/$genesis_user/run/runtime" ]; then
    cp -R "/home/$genesis_user/run/runtime" "/$root_dir/$genesis_user/server/$server_dir/run/" || exit 1
fi

echo "Unlink previous run and link it to the run dir of the current installation" 2>&1 | tee -a "$LOG"
if [ -L "/home/$genesis_user/run" ]; then
    unlink "/home/$genesis_user/run" || exit 1
fi
if [ -d "/home/$genesis_user/run" ]; then
    rm -rf "/home/$genesis_user/run"
    echo "detected run folder as a directory instead of a symlink, this folder has been deleted" 2>&1 | tee -a "$LOG"
fi

ln -s "/$root_dir/$genesis_user/server/$server_dir/run/" "/home/$genesis_user/run" || exit 1
chown -R "$genesis_user:$genesis_grp" "/home/$genesis_user/run" || exit 1

#Copy web if exists
echo "Check if web is being deployed ..." 2>&1 | tee -a "$LOG"
if [ -f "/tmp/genesis_product_name_web.tar.gz" ]; then
    echo "Web is being deployed too ... " 2>&1 | tee -a "$LOG"
    mkdir -p "/$root_dir/$genesis_user/web-$server_dir" || exit 1
    cd "/$root_dir/$genesis_user/web-$server_dir" || exit 1
    tar -xf /tmp/genesis_product_name_web.tar.gz &> /dev/null || exit 1
    echo "Unlink old web installation and point it to the new web folder" 2>&1 | tee -a "$LOG"
    if [ -L $web_path ]; then
        unlink $web_path || exit 1
    fi
    ln -s "/$root_dir/$genesis_user/web-$server_dir/" $web_path || exit 1
    rm -f /tmp/genesis_product_name_web.tar.gz || exit 1
    ls -tp "/$root_dir/$genesis_user/" | grep "web-*" | grep  '/$' | tail -n +5 | xargs -I {} rm -rf -- "/$root_dir/$genesis_user/{}"
fi
chown -R "$genesis_user:$genesis_grp" "/$root_dir/$genesis_user" || exit 1

# Set up bashrc
echo "Setting up bashrc for the $genesis_user if its not present" 2>&1 | tee -a "$LOG"
if grep --quiet GENESIS_HOME -ic "/home/$genesis_user/.bashrc"; then
    echo "bashrc already set"
else
    {
        echo "export GENESIS_HOME=\$HOME/run/"
        echo "[ -f \$GENESIS_HOME/genesis/util/setup.sh ] && source \$GENESIS_HOME/genesis/util/setup.sh"
        echo "export GROOVY_HOME=/opt/groovy"
        echo "PATH=\$GROOVY_HOME/bin:\$PATH"
    } >> /home/"$genesis_user"/.bashrc || exit 1
    echo "bashrc setup complete..." 2>&1 | tee -a "$LOG"
fi

if [ $run_genesis_clear_codegen_cache = "true" ]; then
    # Run command to clear cache
    if genesis_is_installed; then
        echo "Running Genesis cache clear command" 2>&1 | tee -a "$LOG"
        if which ClearCodegenCache &> /dev/null; then
            runuser -l "$genesis_user" -c "ClearCodegenCache"
        else
            runuser -l "$genesis_user" -c "JvmRun -modules=genesis-environment global.genesis.environment.scripts.ClearCodegenCache"
        fi
    fi
fi

if [ $run_genesis_install = "true" ]; then
    # Run genesisInstall
    echo "Running Genesis Install script" 2>&1 | tee -a "$LOG"
    if [ $skip_install_hooks = "true" ]; then
        runuser -l "$genesis_user" -c 'genesisInstall --ignoreHooks'
    else
        runuser -l "$genesis_user" -c 'genesisInstall'
    fi
    install_error_code=$?
    if [ $install_error_code -ne 0 ]; then
        echo "Genesis $genesis_user genesisInstall has failed at $(date)" 2>&1 | tee -a "$LOG"
        exit 1
    fi
    echo "Genesis $genesis_user genesisInstall finished at $(date)" 2>&1 | tee -a "$LOG"

    # kill server
    if genesis_is_running; then
        echo "Detected Genesis processes running, attempting to kill them..." 2>&1 | tee -a "$LOG"
        if genesis_is_installed; then
            runuser -l "$genesis_user" -c 'echo y | killServer --all'
            runuser -l "$genesis_user" -c 'echo y | killDaemon'
        else
            echo "Unable to stopt the processes" 2>&1 | tee -a "$LOG"
            exit 1
        fi
    fi

    # Restore keys
    if [ -d /tmp/keys ]; then
        echo "keys do not exist in runtime. Restoring backup" 2>&1 | tee -a "$LOG"
        cp -r /tmp/keys /home/"$genesis_user"/run/runtime/ || exit 1
        echo "Backup keys restored, cleaning up" 2>&1 | tee -a "$LOG"
        rm -rf /tmp/keys/ || exit 1
        chown -R "$genesis_user:$genesis_grp" /home/axes/run/runtime/keys || exit 1
    fi
fi

if [ $run_genesis_remap = "true" ]; then
    # Run Remap
    echo "Running Remap" 2>&1 | tee -a "$LOG"
    runuser -l "$genesis_user" -c 'echo y | remap --commit --force'
    remap_error_code=$?
    if [ $remap_error_code -ne 0 ]; then
        echo "Genesis $genesis_user remap has failed at $(date)" 2>&1 | tee -a "$LOG"
        exit 1
    fi
    echo "Genesis $genesis_user RPM install finished at $(date)" 2>&1 | tee -a "$LOG"
fi


if [ $start_processes = "true" ]; then
    #Start the server
    echo "/tmp/genesis_install.conf file absent or run_exec not defined .... Starting servers ...." 2>&1 | tee -a "$LOG"
   runuser -l "$genesis_user" -c 'startServer'
fi

echo "Install.sh has completed ..." 2>&1 | tee -a "$LOG"

echo "COMPLETED" > /tmp/genesis_installation_status

%changelog