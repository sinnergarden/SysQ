# Use Case Template — 新增 Use Case 步骤

> 新增一个 use case 的最小步骤。

## 步骤

1. **确定 domain**
   - 属于哪个现有 domain？在 `docs/requirements/domains/` 中找到对应文件。
   - 如果不在任何 domain 中，需新增一个 domain 文件。

2. **在 domain 文件中新增 `## UC_XXX` 章节**
   - 复制现有一个 UC 的结构作为模板。
   - 必填章节：Status, Source, User Goal, Scope, Inputs, Outputs, Canonical Entrypoints, Key Artifacts, Required Checks, Owner Agent, Allowed Paths, Forbidden Paths, Open Questions.

3. **在 `harness_map.yaml` 中新增同名 UC ID**
   - 必填字段：`domain`, `status`, `owner_agent`, `entrypoints`, `artifacts`, `checks`, `allowed_paths`, `forbidden_paths`。
   - 可选字段：`supporting_tools`, `legacy_entrypoints`, `prompt_templates`, `notes`。

4. **在 `01_usecase_index.md` 中新增一行**

5. **运行验证**
   ```bash
   python harness/checks/check_usecase_registry.py
   ```

## 注意事项

- `allowed_paths` 使用具体文件路径或子目录，不要用顶层 `scripts/` 通配。
- 如果 entrypoint 还不存在，写 `TBD`。
- 如果 status 是 `merged` / `deprecated` / `archived`，harness check 会跳过部分检查。
