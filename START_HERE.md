# V4 RL：在现有 V3 仓库上更新

这是单局在线强化学习实验，不是已经经过大量对局预训练的模型。
V1 用户报告分数 1.73，V3 用户报告 1.85；V4 还没有实战成绩。

1. 下载 `mech-royale-v4-rl-update.zip` 到 Downloads，不要解压到仓库。
2. 进入原来的 `mech-royale-v1` Git 仓库目录，确认没有未提交改动。
3. 将更新包解压到临时目录，从原仓库运行其中的 `apply_update.py`。
4. 运行 `python3 -B -m unittest discover -s . -v`，预期 48 项测试通过。
5. 运行 `python3 demo_sarsa.py`，观察教学例子中的价值变化。这不是真实游戏训练。
6. 运行 `python3 check/check.py -- python3 -B -u bot.py`，预期 PASS。
7. git add、commit、push 后，在比赛页面使用原仓库地址提交新的完整 SHA。

不需要创建新 GitHub 仓库，不需要在本机训练大型模型。
每局 Q 表重新初始化，不会在连续提交或多局评分之间自动积累经验。
详细公式、选择范围、奖励和局限见 `V4_NOTES.md`。
