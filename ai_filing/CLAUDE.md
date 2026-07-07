# ELN 速记收件箱 — AI 归档说明

当用户说“归档 ELN 收件箱”“把收件箱整理进实验记录”之类时，按下面做。
目标：把用户随手记的口语/照片速记，提炼成规范实验记录，**直接写进对应实验的对应步骤**。
但**先把完整计划列给用户看，等他明确确认后再写**。绝不未经确认就写入或新建实验。

本地接口：`http://127.0.0.1:8600`（本机访问免密码）。

## 一、读取

1. 取待归档速记：`GET /api/inbox?status=pending`
   每条含：`id`、`text`（口语文字，可能空）、`image_urls`（图片直链数组）、`audio_url`、`hinted_experiment_id`（用户可能已标的实验，可空）、`created_at`。
   - 有图片就打开 `image_urls`（形如 `http://127.0.0.1:8600/photos/inbox/<id>/<file>`）看清内容；也可直接读本机 `~/ELN_Data/photos/<image_path>`。
   - 只有 `audio_url` 而 `text` 为空 = 语音还没转写，别猜内容，提醒用户。

2. 了解实验与步骤：
   `GET /api/experiment_summaries` → 实验列表（`id`、`name`、进度）。
   对相关实验：`GET /api/experiments/{id}/steps` → 每步含 `id`、`step_index`、`title`、`description`、`fields`（每个 `key`/`label`/`type`/`options`）、`values`（当前已填值）。

## 二、整理，并把计划发给用户（先别写）

**先记住：速记文字多半是语音输入转写来的**，可能有同音字、错字、断句混乱。请结合用户之前的速记和现有实验的上下文（步骤名、试剂、术语、进度）推断他到底在说什么，别被识别错字带偏；拿不准的地方在计划里标出来问他，别硬猜也别编造。

逐条速记：
- 提炼关键信息：去掉语音输入的重复和啰嗦，**忠实原意、不要编造**用户没说的结论。
- 判断它属于哪个实验、哪一步、该填哪些字段。`hinted_experiment_id` 有值时优先。
- **数值只有用户明确说了才填**；带单位只填数字部分，绝不编造数字。
- 一条速记的内容可以**拆开写到多个步骤/字段**（分散落位）。
- 那一步**没有合适的字段**时，可以给这步**新增一个字段**来承载，或写进该步备注。
- 长段观察、解释、异常、AI 总结写进该步 Markdown 记录：`values["__eln_step_notes"]`。可以使用 Markdown；若已有内容，追加新段落，不要覆盖旧内容。
- 明显不属于任何现有实验的，**提议新建实验**（给出实验名、步骤结构；结构参考仓库根目录 `ELN_Protocol_Format.md` 和 `protocol_templates/`）。

把上面整理成一份**清单**发给用户：每条速记 → 目标实验/步骤 → 要写入的值 / 要新增的字段 / 要新建的实验。
**然后停下，等用户确认。**

## 三、用户确认后再写（颗粒接口，直接写）

写之前先记下每处的**旧值**，方便用户让你撤销。

- **写入某步**：先 `GET /api/steps/{step_id}` 拿当前 `values` 和 `fields`；把要写的值并进 `values`；
  `PATCH /api/steps/{step_id}`，body `{"values_json": "<整个 values 的 JSON 字符串>"}`。
  Markdown 记录写进 `values["__eln_step_notes"]`，保留旧内容并追加。
- **新增字段**（当前步没有合适的框时）：在原 `fields` 数组后追加 `{"key","label","type"，需要时"options"}`，
  同一次 `PATCH` 传 `{"fields_json": "<整个 fields 的 JSON 字符串>", "values_json": "<含新 key 值的 values>"}`。
- **新建实验**：`POST /api/experiments`，body `{"name":"...","protocol_json":"<ProtocolDefinition 的 JSON 字符串>"}`；
  建好后按上面往它的步骤写。
- **每条写完，标记已归档**：`POST /api/inbox/{id}/filed`，body
  `{"experiment_id":3,"step_id":12,"summary":"一句话说明写了什么"}`
  （把这条移出待办、留档给用户在 `/inbox` 回看）。
  注意：归档后这条的**录音会自动删除**以省空间，之后无法再重听——所以务必先把关键内容都写进实验记录，别依赖事后再听。

## 四、汇报

全部写完，逐条告诉用户：写到了哪个实验哪一步、加了什么字段、建了什么实验。
哪条放错了，用户会让你改或撤销——你用 `PATCH` 把该步 `values_json`/`fields_json` 还原回旧值即可。

## 规则

- **先计划、后写入**：没得到用户明确确认前，不 `PATCH` 任何 step、不 `POST /api/experiments`。
- 忠实、可核对；证据不足就在计划里说明，别硬编。
- 旧的 `POST /api/inbox/{id}/proposal` 和 `/apply` 已不再使用；直接用颗粒接口写，再 `/filed` 标记。
