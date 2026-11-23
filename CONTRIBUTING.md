# 参与贡献

1. 只使用 Python 标准库，不引入第三方运行时依赖；前端不引入第三方库。
2. 新工具用 `@tool` 注册，并声明 JSON Schema、副作用类别（read / compute / runtime / llm / asset）、调度池和超时。
3. 有副作用的工具必须只写自己的步骤目录或 `wangsi/` 运行目录。
4. 修改签核判定、解析器或守卫逻辑时，同时补充 `cloud/tests` 中的测试，并通过：

   ```bash
   cd cloud && python3 -m unittest discover tests
   ```

5. 提交前运行 `bin/wangsictl selftest`。
