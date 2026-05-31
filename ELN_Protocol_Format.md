# ELN App Protocol JSON 格式说明

## 1. 用途

Protocol JSON 是 ELN App 的实验模板。它定义一个实验包含哪些步骤、每一步的说明、计时器、拍照要求、记录字段，以及可选的预设储存物品。

当你从 protocol 新建实验时，ELN App 会复制一份 protocol 作为该实验的运行快照。因此：

- 后续修改协议库模板，不会自动影响已经创建的实验
- 实验过程中修改步骤说明、数字、字段值，只影响当前实验
- 实验结束后可以再临时添加储存物品并登记 Box 位置

---

## 2. 顶层结构

最外层必须是一个 JSON object。

```json
{
  "protocol_name": "Colony PCR",
  "version": "1.0",
  "author": "Yanchang",
  "storage_items": [],
  "steps": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `protocol_name` | string | 是 | 协议名称，显示在协议库和新建实验流程中 |
| `version` | string | 否 | 版本号，建议写字符串，例如 `"1.0"` |
| `author` | string | 否 | 作者 |
| `steps` | array | 是 | 实验步骤数组，建议至少 1 步 |
| `storage_items` | array | 否 | 预设储存物品。只有实验开始前就确定要储存什么时才写；不确定就写 `[]` |

---

## 3. 步骤 `steps`

每个步骤是一个 object。

```json
{
  "title": "PCR 扩增",
  "description": "将反应管放入 PCR 仪，运行 30 分钟",
  "timer_seconds": 1800,
  "has_camera": false,
  "camera_required": false,
  "fields": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 是 | 步骤标题 |
| `description` | string | 是 | 步骤说明。支持 Markdown，包括标题、列表、表格、加粗、行内代码和代码块 |
| `timer_seconds` | number 或 null | 否 | 计时秒数。`0` 或 `null` 表示无计时器 |
| `has_camera` | boolean | 否 | 是否显示照片上传 / 拍照区域 |
| `camera_required` | boolean | 否 | 照片是否原则上必需。目前不会强制阻止实验推进，可后续补照片 |
| `fields` | array | 否 | 本步骤要记录的数据字段。没有就写 `[]` |

### 计时写法

`timer_seconds` 必须写秒数：

| 实际时间 | 写法 |
|---|---|
| 无计时器 | `0` 或 `null` |
| 30 秒 | `30` |
| 5 分钟 | `300` |
| 30 分钟 | `1800` |
| 2 小时 | `7200` |

---

## 4. 多行步骤说明与 Markdown

`description` 支持多行和 Markdown，但 JSON 字符串里换行必须写成 `\n`。

正确：

```json
"description": "第一段说明\n第二段说明\n第三段说明"
```

错误：

```json
"description": "第一段说明
第二段说明"
```

后者不是合法 JSON。

---

### Markdown 表格示例

```json
"description": "### 反应体系\n| 组分 | 体积 |\n|---|---:|\n| 2x Mix | 10 µL |\n| 模板 | 1 µL |\n\n- 冰上操作\n- 轻轻混匀"
```

常用支持格式：

- 标题：`#`、`##`、`###`
- 无序列表和有序列表
- Markdown 表格
- 加粗、斜体、行内代码
- 三反引号代码块

如果想修改整段说明，进入实验执行页后点击“编辑整段说明”。

---

## 5. 记录字段 `fields`

每个 field 是一个 object。

```json
{
  "key": "template_volume",
  "label": "模板用量 (µL)",
  "type": "number",
  "default": "1",
  "required": true,
  "options": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | 是 | 程序内部保存用 key。同一步内必须唯一，建议小写英文加下划线 |
| `label` | string | 是 | 显示给人的字段名 |
| `type` | string | 是 | 只能写 `"text"`、`"number"`、`"dropdown"` |
| `default` | string | 否 | 默认值。建议都写成字符串；没有默认值写 `""` |
| `required` | boolean | 否 | `true` 表示完成步骤前必须填写 |
| `options` | array | 否 | 只有 `dropdown` 必须提供选项；其他类型写 `[]` |

### 文本字段

```json
{
  "key": "primer_f",
  "label": "正向引物编号",
  "type": "text",
  "default": "",
  "required": true,
  "options": []
}
```

### 数字字段

```json
{
  "key": "annealing_temp",
  "label": "退火温度 (°C)",
  "type": "number",
  "default": "60",
  "required": false,
  "options": []
}
```

注意：`number` 主要用于界面提示，目前不会自动校验单位或范围。

### 下拉字段

```json
{
  "key": "result",
  "label": "结果判断",
  "type": "dropdown",
  "default": "阳性",
  "required": true,
  "options": ["阳性", "阴性", "非特异性扩增"]
}
```

`default` 最好是 `options` 里的某一项。

---

## 6. 储存物品 `storage_items`

如果实验开始前已经知道最后要储存哪些东西，可以在顶层写 `storage_items`。

```json
"storage_items": [
  {
    "key": "pcr_product",
    "label": "PCR 产物",
    "tube_type": "1.5mL EP管",
    "default_box": "PCR_Box",
    "notes_template": "PCR product"
  }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 程序内部 key，建议英文或下划线 |
| `label` | string | 样品显示名称 |
| `tube_type` | string | 管型，例如 `1.5mL EP管`、`冻存管` |
| `default_box` | string | 默认 Box 名称。当前只是提示，不会自动创建 Box |
| `notes_template` | string | 默认备注 |

如果实验前不知道会储存什么，推荐写：

```json
"storage_items": []
```

---

## 7. 实验结束后临时添加储存物品

更符合真实实验的方式是：实验做到最后，在“实验收尾 / 储存物品登记”页面临时添加本次实际产生的样品。

输入格式：

```text
名称 | 管型 | 备注
```

例如：

```text
PCR 产物 | 1.5mL EP管 | Colony #1
菌液甘油管 | 冻存管 | strain X
```

也可以只写名称：

```text
PCR 产物
```

添加后，程序会让你选择 Box，并点击网格里的具体位置。若目标位置已有内容，覆盖前会要求确认。

---

## 8. 给 AI 的生成提示词

```text
请把下面实验流程整理成 ELN App protocol JSON。

要求：
1. 必须输出合法 JSON，不要解释；description 字段内部可以使用 Markdown 文本。
2. 顶层包含 protocol_name、version、author、storage_items、steps。
3. 每个 step 包含 title、description、timer_seconds、has_camera、camera_required、fields。
4. timer_seconds 必须是秒数；无计时器写 0。
5. description 如果有多段、列表或表格，请用 \n 表示换行。
6. fields 的 type 只能是 text、number、dropdown。
7. 每个 field 包含 key、label、type、default、required、options。
8. 如果实验开始前不能确定要储存什么，storage_items 写 []。
```

---

## 9. 常见错误

- 不要在 JSON 里写注释
- 字符串必须使用双引号
- `true` / `false` 必须小写
- `timer_seconds` 写秒数，不写分钟数
- `description` 多行、列表和 Markdown 表格换行要用 `\n`
- `dropdown` 必须有 `options`
- `field.key` 同一步内不能重复
- `default_box` 不会自动创建 Box
- 已创建实验里的 `description` 可单独修改，不会影响协议库模板
- 协议库模板修改不会影响已经创建的实验

---

## 10. 完整示例

```json
{
  "protocol_name": "Colony PCR",
  "version": "1.0",
  "author": "Yanchang",
  "storage_items": [],
  "steps": [
    {
      "title": "配制 PCR 反应体系",
      "description": "在冰上配制以下反应体系（总体积 20 µL）\n加入模板、引物和 Mix，轻轻混匀。",
      "timer_seconds": 0,
      "has_camera": false,
      "camera_required": false,
      "fields": [
        {
          "key": "polymerase",
          "label": "DNA 聚合酶",
          "type": "text",
          "default": "Phanta Max",
          "required": true,
          "options": []
        },
        {
          "key": "template_volume",
          "label": "模板用量 (µL)",
          "type": "number",
          "default": "1",
          "required": true,
          "options": []
        }
      ]
    },
    {
      "title": "PCR 扩增",
      "description": "将反应管放入 PCR 仪。\n程序：94°C 30s，55°C 30s，72°C 90s，共 30 个循环。",
      "timer_seconds": 1800,
      "has_camera": false,
      "camera_required": false,
      "fields": [
        {
          "key": "annealing_temp",
          "label": "实测退火温度 (°C)",
          "type": "number",
          "default": "60",
          "required": false,
          "options": []
        }
      ]
    },
    {
      "title": "琼脂糖凝胶电泳",
      "description": "取 5 µL PCR 产物上样，100V 跑胶 30 分钟。\n结束后拍照记录条带。",
      "timer_seconds": 1800,
      "has_camera": true,
      "camera_required": false,
      "fields": [
        {
          "key": "band_size",
          "label": "目的条带大小 (bp)",
          "type": "number",
          "default": "",
          "required": false,
          "options": []
        },
        {
          "key": "result",
          "label": "结果判断",
          "type": "dropdown",
          "default": "阳性",
          "required": true,
          "options": ["阳性", "阴性", "非特异性扩增"]
        }
      ]
    }
  ]
}
```
