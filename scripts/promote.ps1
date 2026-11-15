[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('beta','rc','main')][string]$Target)
$ErrorActionPreference = 'Stop'
$sourceBranch = @{beta='alpha';rc='beta';main='rc'}[$Target]
gh pr create --repo BANG404/Codex_Terminalhook --base $Target --head $sourceBranch --title "chore(release): promote $sourceBranch to $Target" --body "Promote the checked $sourceBranch source into $Target. Publication remains a separate manual Release workflow."
if ($LASTEXITCODE -ne 0) { throw 'Promotion PR was not created' }
