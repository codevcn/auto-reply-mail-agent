import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('103.147.123.63', port=22, username='vmadmin', password='TC_54kIEKo_U')
cmd = "echo 'TC_54kIEKo_U' | sudo -S docker compose -f /opt/mail-agent/compose.yaml logs --tail=40 mail-agent-worker"
stdin, stdout, stderr = ssh.exec_command(cmd)
print("Worker logs:\n" + stdout.read().decode())
ssh.close()
