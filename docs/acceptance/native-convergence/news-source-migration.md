# 原生资讯雷达来源迁移账本

生成日期：2026-08-28
固定基线：`810204efd317000aafa9836f8e2996ae4b1600ca`
upstream 参考：`ab4ffa077e0b1806fc53164dc7b28731f834e79e`
目标：把 108 行基线配置折叠为 106 个唯一内置来源，并保留 A2 的历史决策语义。该表不是当前逐源健康证明。

## 汇总结论

- 当前内置来源：106；upstream 唯一来源：106；名称与 URL 成对匹配：106。
- 固定基线的 Engadget 与少数派各有一条完全重复记录；本 Work 仅折叠重复项，没有以失败观测删除来源。
- A2 冻结决策表含 24 行，其中 1 行是单次 success 观测；其余 23 行仍为 16 partial + 7 failure，结论严格保持为 21 `观察`、1 `需要凭据`、1 `需要许可证`。
- 所有来源均为公开 RSS/Atom 配置、无需用户 Key；内容版权和再分发权仍服从各发布方条款，未逐源核验许可证，因此只聚合标题、链接、来源与公开时间，不声称内容再分发授权。
- 运行时以 canonical `source_id` 关联来源；Radar cache/API 不保存 feed URL，来源管理只公开 scheme + host。自定义原始 URL 仅存在于隔离的用户配置文件和抓取进程内存。

## 逐源账本

| 发布方 | URL | 类型 | 赛道 | upstream | 当前内置 | A2 冻结状态 | 访问 | 许可证 | Key | 允许结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OpenAI | https://openai.com/news/rss.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Google Research | https://research.google/blog/rss/ | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Hugging Face | https://huggingface.co/blog/feed.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 量子位 | https://www.qbitai.com/feed | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| MIT Tech Review AI | https://www.technologyreview.com/topic/artificial-intelligence/feed | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| The Verge AI | https://www.theverge.com/rss/ai-artificial-intelligence/index.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| arXiv cs.AI | https://export.arxiv.org/rss/cs.AI | RSS/Atom | ai | YES | YES | failure / TLS / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| KDnuggets | https://www.kdnuggets.com/feed | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| MarkTechPost | https://www.marktechpost.com/feed/ | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| BAIR Blog | https://bair.berkeley.edu/blog/feed.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Import AI | https://importai.substack.com/feed | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| DeepMind | https://deepmind.google/blog/rss.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 智东西 | https://zhidx.com/rss | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 机器之心 | https://wechat2rss.xlab.app/feed/51e92aad2728acdd1fda7314be32b16639353001.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 新智元 | https://wechat2rss.xlab.app/feed/ede30346413ea70dbef5d485ea5cbb95cca446e7.xml | RSS/Atom | ai | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| DIGITIMES | https://www.digitimes.com/rss/daily.xml | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| SemiAnalysis | https://semianalysis.substack.com/feed | RSS/Atom | semi | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Semiconductor Engineering | https://semiengineering.com/feed/ | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| EE Times | https://www.eetimes.com/feed/ | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| IEEE Spectrum 半导体 | https://spectrum.ieee.org/feeds/topic/semiconductors.rss | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| SemiWiki | https://semiwiki.com/feed/ | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Semiconductor Today | https://www.semiconductor-today.com/rss/news.xml | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Electronics Weekly | https://www.electronicsweekly.com/feed/ | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| All About Circuits | https://www.allaboutcircuits.com/rss/news/ | RSS/Atom | semi | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| The Robot Report | https://www.therobotreport.com/feed/ | RSS/Atom | robot | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| IEEE Spectrum 机器人 | https://spectrum.ieee.org/feeds/topic/robotics.rss | RSS/Atom | robot | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Robohub | https://robohub.org/feed/ | RSS/Atom | robot | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Robotics & Automation | https://roboticsandautomationnews.com/feed/ | RSS/Atom | robot | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Robotics Business Review | https://www.roboticsbusinessreview.com/feed/ | RSS/Atom | robot | YES | YES | partial / redirect / 1 item; final URL is HTTP; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Electrek | https://electrek.co/feed/ | RSS/Atom | auto | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| InsideEVs | https://insideevs.com/rss/articles/all/ | RSS/Atom | auto | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| The Verge Transport | https://www.theverge.com/rss/transportation/index.xml | RSS/Atom | auto | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| TechCrunch Transport | https://techcrunch.com/category/transportation/feed/ | RSS/Atom | auto | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| CnEVPost | https://cnevpost.com/feed/ | RSS/Atom | auto | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| CleanTechnica | https://cleantechnica.com/feed/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Utility Dive | https://www.utilitydive.com/feeds/news/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| pv magazine | https://www.pv-magazine.com/feed/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Energy Storage News | https://www.energy-storage.news/feed/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| OilPrice | https://oilprice.com/rss/main | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Canary Media | https://www.canarymedia.com/articles.rss | RSS/Atom | energy | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| PV Tech | https://www.pv-tech.org/feed/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Renewable Energy World | https://www.renewableenergyworld.com/feed/ | RSS/Atom | energy | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 国际能源网 | https://www.in-en.com/feed/rss.php?mid=21 | RSS/Atom | energy | YES | YES | failure / connection / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| STAT News | https://www.statnews.com/feed/ | RSS/Atom | bio | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Endpoints News | https://endpts.com/feed/ | RSS/Atom | bio | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| FierceBiotech | https://www.fiercebiotech.com/rss/xml | RSS/Atom | bio | YES | YES | failure / empty_payload / HTTP 200; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| FiercePharma | https://www.fiercepharma.com/rss/xml | RSS/Atom | bio | YES | YES | failure / empty_payload / HTTP 200; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| BioPharma Dive | https://www.biopharmadive.com/feeds/news/ | RSS/Atom | bio | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| GEN | https://www.genengnews.com/feed/ | RSS/Atom | bio | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Nature Biotech | https://www.nature.com/nbt.rss | RSS/Atom | bio | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| SpaceNews | https://spacenews.com/feed/ | RSS/Atom | space | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Space.com | https://www.space.com/feeds/all | RSS/Atom | space | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Spaceflight Now | https://spaceflightnow.com/feed/ | RSS/Atom | space | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Payload | https://payloadspace.com/feed/ | RSS/Atom | space | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| NASA | https://www.nasa.gov/news-release/feed/ | RSS/Atom | space | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| NASASpaceflight | https://www.nasaspaceflight.com/feed/ | RSS/Atom | space | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Krebs on Security | https://krebsonsecurity.com/feed/ | RSS/Atom | security | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| The Hacker News | https://feeds.feedburner.com/TheHackersNews | RSS/Atom | security | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| BleepingComputer | https://www.bleepingcomputer.com/feed/ | RSS/Atom | security | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Dark Reading | https://www.darkreading.com/rss.xml | RSS/Atom | security | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| SecurityWeek | https://www.securityweek.com/feed/ | RSS/Atom | security | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| TechCrunch | https://techcrunch.com/feed/ | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| The Verge | https://www.theverge.com/rss/index.xml | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Ars Technica | https://feeds.arstechnica.com/arstechnica/index | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Hacker News | https://hnrss.org/frontpage | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| WIRED | https://www.wired.com/feed/rss | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Techmeme | https://www.techmeme.com/feed.xml | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 36氪 | https://36kr.com/feed | RSS/Atom | tech | YES | YES | failure / parse / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 钛媒体 | https://www.tmtpost.com/rss.xml | RSS/Atom | tech | YES | YES | success / HTTP 200 / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 仅历史单次成功；不得外推当前或长期可用 |
| IT之家 | https://www.ithome.com/rss/ | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| GitHub Blog | https://github.blog/feed/ | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Stratechery | https://stratechery.com/feed/ | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 虎嗅 | https://rss.huxiu.com/ | RSS/Atom | tech | YES | YES | failure / TLS / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 动点科技 | https://cn.technode.com/feed/ | RSS/Atom | tech | YES | YES | failure / HTTP 403 / 0 items; 需要凭据 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 月光博客 | https://www.williamlong.info/rss.xml | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Solidot | https://www.solidot.org/index.rss | RSS/Atom | tech | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 白鲸出海 | https://www.baijingapp.com/feed | RSS/Atom | tech | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Engadget | https://www.engadget.com/rss.xml | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 9to5Mac | https://9to5mac.com/feed/ | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 9to5Google | https://9to5google.com/feed/ | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| GSMArena | https://www.gsmarena.com/rss-news-reviews.php3 | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Android Authority | https://www.androidauthority.com/feed/ | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| DPReview | https://www.dpreview.com/feeds/news.xml | RSS/Atom | consumer | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 少数派 | https://sspai.com/feed | RSS/Atom | consumer | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| CNBC | https://www.cnbc.com/id/100003114/device/rss/rss.html | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Financial Times | https://www.ft.com/rss/home | RSS/Atom | macro | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| WSJ Markets | https://feeds.a.dj.com/rss/RSSMarketsMain.xml | RSS/Atom | macro | YES | YES | partial / stale_data / HTTP 200, latest 2025-01-28; 需要许可证 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| MarketWatch | https://feeds.marketwatch.com/marketwatch/topstories/ | RSS/Atom | macro | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Yahoo Finance | https://finance.yahoo.com/news/rssindex | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 华尔街见闻 | https://dedicated.wallstreetcn.com/rss.xml | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Finextra | https://www.finextra.com/rss/headlines.aspx | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| SEC | https://www.sec.gov/news/pressreleases.rss | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Federal Reserve | https://www.federalreserve.gov/feeds/press_all.xml | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Seeking Alpha | https://seekingalpha.com/market_currents.xml | RSS/Atom | macro | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| 东方财富股票 | http://rss.eastmoney.com/rss_stock.xml | RSS/Atom | macro | YES | YES | partial / insecure_transport / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 东方财富资讯 | http://rss.eastmoney.com/rss_partener.xml | RSS/Atom | macro | YES | YES | partial / insecure_transport / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| 经济观察网 | http://www.eeo.com.cn/rss.xml | RSS/Atom | macro | YES | YES | partial / insecure_transport / 0 items; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Nature News | https://www.nature.com/nature.rss | RSS/Atom | science | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| ScienceDaily | https://www.sciencedaily.com/rss/all.xml | RSS/Atom | science | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Quanta Magazine | https://api.quantamagazine.org/feed/ | RSS/Atom | science | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| New Scientist | https://www.newscientist.com/feed/home/ | RSS/Atom | science | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| Live Science | https://www.livescience.com/feeds/all | RSS/Atom | science | YES | YES | partial / redirect / 1 item; 观察 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留配置但不得标记为当前成功；需当前探测 |
| MIT News | https://news.mit.edu/rss/research | RSS/Atom | science | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Science News | https://www.sciencenews.org/feed | RSS/Atom | science | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |
| Ars Technica Science | https://feeds.arstechnica.com/arstechnica/science | RSS/Atom | science | YES | YES | 无降级决策行 | 公开无 Key | 发布方条款；未逐源核验再分发许可 | NO | 保留内置配置；未作当前单源健康承诺 |

## 冻结决策核对

- `DEGRADED_DECISIONS=23`
- `PARTIAL=16`；`FAILURE=7`
- `OBSERVE=21`；`CREDENTIAL=1`；`LICENSE=1`
- `REPAIRED=0`；`UPDATED=0`；`REPLACED=0`；`DISABLED_FROM_A2=0`

A2 冻结状态来自 `docs/data-sources/source-repair-decisions.md`；它保留 2026-08-19 的有界公网观测，不会被本次浏览器中出现的真实新闻内容改写为 PASS。
