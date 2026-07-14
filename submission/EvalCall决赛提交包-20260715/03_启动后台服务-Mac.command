#!/bin/zsh
set -e

export PATH="/opt/homebrew/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
PROJECT="$HOME/AI/比赛合集/美团黑客松"

if [[ ! -x "$PROJECT/scripts/启动决赛公网Demo.command" ]]; then
  PROJECT="$HOME/Desktop/EvalCall决赛运行版"
  if [[ ! -d "$PROJECT/.git" ]]; then
    git clone --branch codex/evalcall-productization-20260712 --single-branch \
      https://github.com/kaijie0074-art/evalcall.git "$PROJECT"
  fi
fi

if [[ ! -x "$PROJECT/scripts/启动决赛公网Demo.command" ]]; then
  echo "没有找到启动脚本。请先阅读 02_比赛现场操作说明.txt。"
  read "?按回车退出……"
  exit 1
fi

exec "$PROJECT/scripts/启动决赛公网Demo.command"
