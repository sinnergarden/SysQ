# SysQ 开发工作流指南 (Dev Workflow Guide)

本指南概述了 SysQ 的标准开发工作流，重点是维护系统稳定性、数据质量和特征覆盖率。

配套文档：
- [PROJECT_TARGETS.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/PROJECT_TARGETS.md)
- [ENVIRONMENT.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ENVIRONMENT.md)
- [PROJECT_RULES.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/rules/PROJECT_RULES.md)
- [ARTIFACT_POLICY.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/rules/ARTIFACT_POLICY.md)
- [ENTRYPOINT_POLICY.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/manuals/ENTRYPOINT_POLICY.md)

---

## 1. 特征覆盖率检查 (Feature Coverage Check)

**目标**：验证新增特征字段是否有有效数据并监测空置率。

**触发时机**：
- 在 `qsys/data/adapter.py` 或 `collector.py` 新增或调整字段后。
- 更新数据后发现某些字段全是 NaN 或空置率过高。
- 回测或训练中出现字段缺失告警时。

**步骤**：
1.  运行特征覆盖率测试：
    ```bash
    python -m unittest tests/test_feature_coverage.py
    ```

2.  如果出现高空置率或缺失字段：
    -   检查 `qsys/data/collector.py` 是否采集了对应字段。
    -   检查 `qsys/data/adapter.py` 是否在 CSV 与 dump_bin 中包含字段。
    -   重新执行数据增量或全量转换。

3.  确保回测日志中不再出现高空置率告警。

**期望结果**：
-   新增字段在测试中至少存在非空值。
-   高空置率字段被明确告警并可追踪修复。

---

## 2. 系统核心验证 (Qsys Verification)

**目标**：确保在修改核心组件（collector, adapter, config）后数据完整性和系统稳定性。

**触发时机**：每次大量重构 `qsys` 内的关键代码时。

**步骤**：

1.  **运行数据质量检查**
    执行集成测试套件以验证数据正确性。
    ```bash
    python tests/test_data_quality.py
    ```
    **验证点**：
    -   仪器列表正确（CSI300）。
    -   数据字段与 `settings.yaml` 匹配。
    -   关键字段（open, close, volume）不为空/NaN。
    -   索引正确设置为 (instrument, datetime)。
    -   **量级检查**：`total_mv` 和 `circ_mv` 应为元（例如大盘股 > 1e11），而不是万。

2.  **检查配置一致性**
    确保 `config/settings.yaml` 与数据需求一致。
    -   `qlib_fields` 不应包含冗余列（例如显式的 'date' 字段导致 NaN）。
    -   确保 `feature_fields` 与预期输出匹配。

3.  **审查日志**
    检查执行日志中是否有关于字段缺失或空 DataFrame 的警告。

---

## 3. 清理训练产物 (Cleanup Artifacts)

**目标**：清理 MLflow、Users 等临时目录，保持仓库整洁。

**触发时机**：当项目中出现 `mlruns/`、`Users/` 等目录，或训练/回测后产生大量临时文件时。

**操作步骤**：
1.  枚举并删除以下目录（如果存在）：
    -   `mlruns/`
    -   `Users/`
    -   `notebooks/mlruns/`
    -   `notebooks/Users/`

---

## 4. 标准教程流程验证 (Tutorial Flow)

**目标**：确保核心演示流程（`tutorial.ipynb`）始终可运行。

**步骤**（9 步）：
1.  **数据获取**：每日数据获取 -> 保存到 Qlib 二进制存储。
2.  **特征处理**：使用 `SysAlpha` 生成特征 -> 验证输入数据（Sanity Check）。
3.  **模型训练（阶段 1）**：在训练集上训练，验证集上验证 -> 确定最佳轮次 -> 在测试集上测量性能。
4.  **模型重训练（阶段 2）**：在合并集（训练+验证）上使用 `1.1 * 最佳轮次` 重训练 -> 在测试集上测量性能。
5.  **模型评估**：评估模型性能（IC，Rank IC，分组分析）。
6.  **静态回测**：在测试期运行回测（静态设置）。
7.  **滚动回测**：在测试期运行滚动回测。
8.  **实盘交易模拟**：模拟真实交易条件（成交，费用，现金管理）。
9.  **交易计划生成**：生成次日 Top K 交易列表。

**验证协议**：
-   **测试脚本**：`tests/test_tutorial_flow.py`
-   **范围**：50 只股票（小样本）。
-   **执行**：在任何修改后运行此脚本，以确保核心流程保持完整。
