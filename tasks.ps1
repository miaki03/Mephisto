#Requires -Version 5.1
<#
.SYNOPSIS
    Mephisto 常用任务。

.DESCRIPTION
    本仓库**零安装即可运行**：包放在仓库根的 `mephisto/`，测试用标准库 unittest。
    所以这里不创建虚拟环境、不调用 pip —— 当前环境里 pip 装任何包都会失败
    （沙箱禁止临时目录里的 rename 与删除），把 `setup` 做成"自检"更诚实。

.EXAMPLE
    .\tasks.ps1 setup     # 自检：Python 版本、能否 import、doctor 是否通过
    .\tasks.ps1 test      # 跑测试
    .\tasks.ps1 doctor    # 体检
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'test', 'doctor', 'version', 'help')]
    [string]$Task = 'help'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# 中文输出：管道重定向时 Python 用本地代码页（GBK），指定 UTF-8 才不乱码
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = $root

$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPy) { $python = $venvPy } else { $python = 'python' }

switch ($Task) {
    'setup' {
        Write-Host '== Mephisto 自检 ==' -ForegroundColor Cyan
        & $python -c "import sys; print('Python', sys.version.split()[0], '>= 3.12:', sys.version_info >= (3, 12))"
        & $python -c "import mephisto; print('import mephisto OK, version', mephisto.__version__)"
        & $python -m mephisto doctor
        Write-Host ('doctor exit = ' + $LASTEXITCODE)
        Write-Host '跑测试：tasks.cmd test' -ForegroundColor DarkGray
    }
    'test' {
        & $python (Join-Path $root 'tests\run.py')
        exit $LASTEXITCODE
    }
    'doctor' {
        & $python -m mephisto doctor
        exit $LASTEXITCODE
    }
    'version' {
        & $python -m mephisto version
        exit $LASTEXITCODE
    }
    default {
        Write-Host 'Mephisto 任务' -ForegroundColor Cyan
        Write-Host '  tasks.cmd setup    自检（零安装）'
        Write-Host '  tasks.cmd test     跑测试'
        Write-Host '  tasks.cmd doctor   体检'
        Write-Host '  tasks.cmd version  版本'
    }
}
