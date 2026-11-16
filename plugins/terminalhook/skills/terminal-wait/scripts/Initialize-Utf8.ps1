# Dot-source in the target PowerShell session before reading or writing text.
# Console encodings govern redirected stdin/stdout and native command output;
# OutputEncoding governs text sent from PowerShell pipelines to native commands.
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
