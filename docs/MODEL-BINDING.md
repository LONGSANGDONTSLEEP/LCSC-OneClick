# 3D 模型整合（1.1）

## 使用

在“一键导出”界面粘贴 C 编号，选择 AD 或 AD + KiCad，重新导出。生成的 `OneClick.PcbLib` 自动包含 STEP 及每个封装的原生 3D Body。打开 PCB Library 的封装，切换到 3D 视图即可检查；模型不依赖外部 STEP 路径。

KiCad 使用外部文件关联：把任务包里的 `OneClick_3D` 文件夹复制到自己的 KiCad 工程根目录。封装的 `${KIPRJMOD}/OneClick_3D/文件.step` 路径会在当前工程中解析。符号库和封装库的昵称均设为 `OneClick`。

1.0 已导出的二维库不会自动改变，需要用 1.1 的“一键导出”重新生成。高级界面的自定义导出仍使用上游二维导出流程。

## 编码与验证

- AD：CFB `Library/Models/Header` 保存模型数量，`Data` 保存模型 GUID 和内嵌标志，编号流保存 zlib 压缩的完整原始 STEP。
- 每个匹配封装新增类型 12 的原生 ComponentBody，模型 GUID 指向上述索引，设置 `MODEL.EMBED=TRUE` 和 `MODEL.MODELTYPE=1`。
- CAD 坐标原点来自封装 head 与 3D 节点 `c_origin`。源画布单位为 10 mil，转换为毫米并翻转画布 Y 轴；源 `z` 转成毫米高度。保留源 X/Y/Z 旋转，KiCad 按其旋转方向取相反角度。
- 不修改 STEP 几何，不基于包围盒猜测模型原点。放置参数和绑定结果写入 `manifest.json`。
- 回读生成的 CFB，解压内嵌 STEP 并与下载的原始数据逐字节比较。
- AltiumSharp 1.0.2 独立读取两个真实器件（C2040、C20197）的源码和 EXE 导出结果：各有一个 ComponentBody，模型类型 1、内嵌标志正确，完整 STEP 能读取，库解析零警告、零错误。

原生字段布局研究来源：[AltiumSharp PcbLibWriter](https://github.com/issus/AltiumSharp/blob/ce72437f30cd54f549601d4e0ca5846d21272150/src/OriginalCircuit.Altium/Serialization/Writers/PcbLibWriter.cs)。互操作坐标和旋转研究来源：[KiCad Altium importer](https://github.com/KiCad/kicad-source-mirror/blob/master/pcbnew/pcb_io/altium/altium_pcb.cpp)。软件不依赖或打包这两个工具。

## 无法绑定时

没有 STEP、缺少唯一模型放置记录、非单位模型缩放、无法匹配封装几何、共享二维封装对应不同模型时，保留已有二维文件并报告原因，器件进入重试列表。不会静默选择冲突模型。

本次未在 Altium Designer 桌面软件中人工验收。源数据也可能有原点、旋转或高度误差；生产前请在 EDA 3D 视图核对实际器件与 PCB。
