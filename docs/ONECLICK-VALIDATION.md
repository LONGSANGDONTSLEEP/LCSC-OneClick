# 一键版本验证记录

日期：2026-10-13（Asia/Shanghai）。Windows 11 x64、Python 3.13.12、PySide6 6.11.1。

- 新增测试通过：混合 BOM 提取与去重、GB18030 CSV 导入、空输入拒绝、真实转换引擎生成四种 AD/KiCad 库、缺失3D报告、ZIP CRC 校验、重复任务保留原目录。
- 在线实测 `C2040` 和 `C20197`：每个器件 STEP、OBJ、SchLib、PcbLib、KiCad 符号、KiCad 封装六种格式全部成功。
- 一键 GUI 源码启动通过，截图检查无截断和重叠。
- 全量回归：562 项测试全部通过（223.434 秒）。修正上游字体测试选到空字体名的环境兼容问题。
- 便携 EXE：主界面、高级界面与在线导出三个冒烟检查均退出码 0；两个器件均完成六种格式。
- GUI 按钮端到端验证：实际触发“一键导出”，两个器件成功，ZIP 存在，完成后按钮恢复可用。
- EXE 中 oneclick、oneclick_export、backend、kicad 的代码对象与当前源代码编译结果逐项相等。
- 本次未在 Altium Designer 和 KiCad 桌面软件中人工打开验证；转换正确性依赖上游引擎及其回归测试。
- 不代表所有器件都有3D，或所有复杂符号、异形封装都能无损转换。

完整测试结果、便携 EXE 冒烟结果和校验值随 Release 提供。

## 1.1 模型绑定验证

- 全量回归 565 项通过（235.156 秒）。新增测试检查嵌入 STEP 的回读、模型 GUID、源坐标单位/旋转/高度、KiCad 相对引用和无模型报告。
- 1.1 便携 EXE 主界面冒烟与两个器件的在线导出均退出码 0。
- AltiumSharp 1.0.2 独立读取源码和 EXE 导出的 PcbLib：2 个封装，各有一个原生 3D Body，均能读取完整内嵌 STEP，库解析零警告、零错误。见 [3D 整合说明](MODEL-BINDING.md)。
- EXE 中 oneclick、oneclick_export、model_binding 代码对象与源码编译结果逐项相等。

## 1.2 C225122 偏移修复

- 全量回归 568 项通过（239.146 秒）。真实 C225122 STEP 验证 X/Y 范围与源投影的吻合，底面 Z=0；验证附加偏移及尺寸不匹配拒绝自动校准。
- 源码及便携 EXE 在线导出 C2040、C20197、C225122 均成功；冻结 EXE GUI 和导出检查退出码 0。
- AltiumSharp 独立读取 EXE 生成的库，3 个封装、3 个内嵌模型关联均正确，库解析零警告、零错误；C225122 读取值为 X=-123.032 mil、Y=12.382 mil、Z=0.394 mil（约 -3.1250 / +0.3145 / +0.0100 mm）。
- EXE 中 oneclick、oneclick_export、model_binding、model_alignment 代码对象与当前源码逐项相等。
- [计算顶视图对比](images/C225122-alignment.png) 显示修正后引脚与焊盘对齐；该图不是 Altium 软件截图。
