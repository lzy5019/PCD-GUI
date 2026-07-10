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
