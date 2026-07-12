# 2026-07-10：FUS–Probe 采集验证模块交接记录

## 项目位置

- 工作区：`D:\Desktop\PCD`
- 主程序：`D:\Desktop\PCD\code\main.py`
- Python 环境：`D:\Desktop\PCD\code\.venv\Scripts\python.exe`

## 本次完成内容

### 1. 左侧 GUI 布局与模式显示

- 真机模式仅显示 ART 采集、信号发生器、PCD 分析和显示设置。
- 回放模式仅显示回放、PCD 分析和显示设置。
- 对比模式仅显示对比设置与 PCD 分析；不相关模块直接隐藏而非禁用。
- 为左侧可滚动设置区加入底部 stretch，修复各设置组收起后被多余高度拉伸、标题/按钮看起来垂直居中的问题。

### 2. 第三个算法选项

新增算法：`交替治疗–探测反馈（FUS–Probe）`

当前仅支持 `Hardware 真机` 模式，定位是**采集验证版**，不计算新指标、不支持回放、不做闭环调压。

专属设置位于“PCD 分析设置”中：

- 探测电压，默认 `4.5 Vpp`
- 治疗电压，默认 `7.0 Vpp`
- 探测后等待，默认 `200 ms`
- 周期，默认 `1000 ms`

频率与 burst cycles 沿用“信号发生器手动设置”：默认可设 `600 kHz`、`6000 cycles`。600 kHz × 6000 cycles = 10 ms。

### 3. 采集时序

每一个 cycle 严格为：

```text
Probe（低声压） → 等待 inter-burst delay → Treatment（治疗声压） → 等待至下一个周期
```

软件实现采用 **ART 先 armed，再总线触发 DG1062 单次 burst** 的顺序：

1. 后台 worker 独占连接发生器；GUI 侧已有连接会先关闭输出、再释放连接。
2. 将发生器配置为 N-cycle、手动触发源、Sync trigger 输出为正沿。
3. 写入本段的 Vpp，并用 `*OPC?` 确认写入完成。
4. ART 采集卡 `StartDeviceAD` 成功后发出 armed 信号。
5. 发送 `*TRG`，发生器输出一次 burst，同时 Sync 脉冲触发采集卡。
6. Probe 完成后切换到 Treatment Vpp，重复该过程。

未采用“修改参数恰好立即输出一次”的副作用；总线手动触发更可控。代码首先使用 `MANual` 触发源；若旧固件报错，会回退尝试 `BUS`。

## 重要物理连线与实际测试前提

- 信号发生器对应通道的 Sync/Trigger Out 必须仍接到 ACTS1000 的 `sync0` 触发输入。
- FUS–Probe 模式必须先在 GUI 中连接信号发生器；未连接时点击开始会被阻止。
- 程序运行结束、停止或异常时 worker 会尝试关闭发生器输出；运行结束后 GUI 会显示未连接，需要重新点击“连接”才可手动控制发生器。
- 首次连机建议先接示波器或不接功放/换能器，确认每次 `*TRG` 只输出一个 6000-cycle burst，并确认 Probe/Treatment 的 Vpp 切换正确。

## 原始数据保存结构

每次运行会在“ART 采集设置”的输出目录下建立独立目录：

```text
fus_probe_YYYYMMDD_HHMMSS/
  probe/
    cycle_000001.csv
  treatment/
    cycle_000001.csv
  manifest.csv
  run_metadata.json
  failed/
```

- `probe/cycle_000001.csv` 与 `treatment/cycle_000001.csv` 是同一个 cycle 的一对数据。
- 只有 Probe 与 Treatment **都成功采集**后，两个 CSV 才会被移动到正式子文件夹，并在 `manifest.csv` 中记录配对关系、时间、Vpp、频率和 cycles。
- 若中途失败或停止，未完成的一段会进入 `failed/`，不会混入正式 probe/treatment 文件夹。
- `run_metadata.json` 保存本次运行的发生器、采集卡、FUS–Probe 设置和估算时长。

## 必须注意：采集窗长度

当前常用设置为：`25 MHz`、`25000 points`，采集窗仅为：

```text
25000 / 25 MHz = 1 ms
```

而 6000 cycles @ 600 kHz 的 burst 为 10 ms。若目标是完整保存整个 burst，应该将采样点数至少设为：

```text
25 MHz × 10 ms = 250000 points
```

程序在采集窗短于 burst 时会在日志中给出 WARNING，但不阻止运行；1 ms 仍可用于先确认触发和 PCD 是否有信号。

## 尚未实现（刻意保留到下一阶段）

- 不计算 Probe/Treatment 的频谱指标。
- 不计算探测质量、治疗/探测谐波比、宽带风险指标。
- 不支持 FUS–Probe 专用回放。
- 不依据探测结果自动改治疗电压。
- 不改动经典 SCD–ICD、IUD、原回放和对比功能。

下一阶段建议顺序：先实际采集若干对 CSV → 核对 Sync、波形与频谱 → 读取 `manifest.csv` 做配对回放 → 再确定固定阈值算法和闭环规则。

## 主要代码位置

- `FusProbeSettings`：`main.py` 约 448 行
- DG1000 手动 burst 配置与触发：`main.py` 约 666 行
- FUS–Probe 采集 worker：`main.py` 约 3670 行
- 算法设置 GUI：`main.py` 约 4339 行
- 开始时将发生器控制权交给 worker：`main.py` 约 5543 行

## 验证已完成

- `python -m py_compile main.py` 通过。
- Offscreen Qt 检查：新算法设置页、结果页、对比页索引正确。
- 无硬件 Fake DG/ACTS 测试：能够生成一对同名 probe/treatment CSV 和一行完整 `manifest.csv`。
- SCPI 命令顺序检查：手动触发源、单次 Vpp 切换、`*TRG` 均被调用。

## Git 记录

分支名：`feature/fus-probe-capture-validation`

本地提交：

```text
48e81e5 新增交替治疗-探测采集验证模块
18b6fd8 加入交替治疗探测测试配置
```

`code/Temp/test.json` 已加入第二个提交，包含当时的本机路径、VISA 地址及测试配置。同步到另一台电脑后可作为导入设置方案使用，但其中的本机路径与设备地址应按实验室电脑实际情况复核。

---

## 2026-07-12：算法与右侧面板设计结论

### 最终要输出的三种生物学状态

FUS–Probe 算法的目标不是泛泛地判断“有没有空化”，而是判定：

```text
1. 未开 BBB
2. 安全开 BBB
3. 有安全风险
```

另保留一个灰色的**信号无效**提示：Probe 信号未能从噪声中可靠分离时，不能把它误判为“未开 BBB”。它只是测量质量状态，不是第四种生物学结论。

### 采用的主算法：Probe 归一化 HE–BE 三态分析

决定采用 HE（Harmonic Emission）和 BE（Broadband Emission）作为主指标，取代对新算法直接套用旧的 SCD–ICD reference 库。

- HE：整数谐波频带能量，表征稳定空化；用于评价治疗是否有效、并形成累计有效剂量。
- BE：避开谐波/超谐波后的宽带能量，表征惯性空化或危险风险；优先级高于开窗判定。
- IUD：仍保留为 burst 内的提前失稳预警；它不是替代 HE/BE，而是补充 BE 可能出现得较晚的问题。
- Probe：不是额外的 reference CSV，而是每一个 cycle 内与治疗 burst 配对的**动态参考**。

每个 cycle 先得到 Probe 和 Treatment 两份 PCD 频谱：

```text
Hp：Probe 的谐波频带功率
Ht：Treatment 的谐波频带功率
Bp：Probe 的宽带功率
Bt：Treatment 的宽带功率
```

建议的 Probe 归一化 HE/BE 形式：

```text
HE_k = treatment 谐波功率 / probe 对应谐波功率，经系统补偿后转 dB
BE_k = treatment 宽带功率 / probe 对应宽带功率，经系统补偿后转 dB
```

可写为频带积分的形式：

\[
HE_k = 10\log_{10}\left(
\frac{1}{|H|}\sum_{m\in H}
\frac{\int_{W_m}PSD_T(f)df}
{C_m\int_{W_m}PSD_P(f)df}
\right)
\]

\[
BE_k = 10\log_{10}\left(
\frac{\int_{W_{BB}}PSD_T(f)df}
{C_{BB}\int_{W_{BB}}PSD_P(f)df}
\right)
\]

其中 `PSD_T` / `PSD_P` 分别为治疗和探测频谱；`W_m` 为第 m 个整数谐波频带；`W_BB` 为排除谐波与超谐波的宽带区。

由于 Treatment 为 7 Vpp、Probe 为 4.5 Vpp，二者不能直接相除。`C_m` 和 `C_BB` 是两种电压下设备/耦合链路固有差异的**固定系统补偿**，应由一次水槽或无微泡标定获得。它不是实验运行时的 reference 库。

### 三态判定逻辑

只累计没有危险迹象的 HE，形成安全稳定空化剂量：

\[
HE_{dose}(k)=\sum_{i=1}^{k}HE_i\cdot
\mathbf{1}(BE_i<\theta_{BE},\ IUD_i<\theta_{IUD})
\]

判定优先级：

```text
Probe 质量 Qp < θprobe
    → 信号无效（灰色）

BEk ≥ θBE，或 IUDk ≥ θIUD
    → 有安全风险（红色）

风险安全，且累计 HEdose < θopen
    → 未开 BBB（蓝色）

风险安全，且累计 HEdose ≥ θopen
    → 安全开 BBB（绿色）
```

这套结构借鉴的关键思想：

1. Konofagou/Sun：HE 控制稳定空化，BE 作为惯性空化安全约束；每个 burst 都计算频谱比值。
2. ThUS/PCI：累计 PCI 比单点峰值 PCI 更能对应 BBB opening，因此“是否开 BBB”应看累计安全 HE 剂量，而不是某一个最大峰值。
3. CEA IUD：burst 内超谐波变化可在明显宽带事件之前预警微泡失稳。
4. Hong Chen：低压超声可作为个体化稳定空化参考；本项目用每个 cycle 的 Probe 取代单独的 Dummy Sonication。
5. ETH 长循环微泡工作：将空化划分为稳定区 SO、过渡区 TR、惯性空化区 IC 和高压区 HP；本项目的“安全开 BBB / 风险”应当沿用这种分区思想。

### 阈值的科学边界

- 运行时不加载“有空化/无空化”reference CSV 库。
- 但 `θprobe`、`θBE`、`θIUD`、`θopen` 不能从文献直接照搬。
- 这些数值必须在本系统上，用 MRI、Evans blue、组织学或其他真实终点数据，把“未开 BBB / 成功开 BBB / 损伤”标定后固定下来。
- 例如 IUD 文献的 8 dB 可以先作为 GUI 的候选显示线，但不能未经本系统验证直接宣称为危险阈值。

### 右侧 FUS–Probe 专属面板设计

建议右侧由上到下显示：

```text
1. 当前三态大标签 + cycle 状态时间轴
   蓝：未开 BBB；绿：安全开 BBB；红：有风险；灰：信号无效

2. HE 与累计 HEdose 曲线
   显示每个治疗 cycle 的 HE、累计安全剂量和 θopen

3. BE 与 IUD 安全曲线
   显示 BE、IUD、θBE、θIUD；风险时突出红色标记

4. 最新一对 Probe / Treatment 频谱叠加图
   标出 HE 谐波频带与 BE 宽带频带，便于确认两段采集是否正确

5. 最新 cycle 参数与判定依据
   cycle 编号、Probe/Treatment Vpp、HE、BE、IUD、HEdose、最终状态

6. 日志
```

### 第二阶段可选增强：α 频谱斜率

CEA 2025 提出的 `α` 用宽带谱的频率斜率区分颅内空化与颞肌等颅外来源。它很适合作为“信号来源可信度”附加指标：当治疗 BE 升高但 α 不符合经颅衰减特征时，避免误触发危险报警。

暂不把 α 放入第一版三态核心，因为它对 PCD 频带、经颅路径和系统几何强依赖，必须先用本系统数据验证。第一版先实现 HE/BE/IUD/累计剂量与三态面板。

### 本次调研来源

- `D:\Desktop\BPLab\组会报告\组会 2026.06.15 PCD前沿调研.pptx`
- 重点复核页：3（HE/BE）、8（累计 PCI）、14（SO/TR/IC/HP 分区）、15（IUD）、16（α）、18–19（个体化 SC baseline）。
