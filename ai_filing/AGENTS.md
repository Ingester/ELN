# ELN 速记收件箱 — AI 归档说明

当用户说“归档 ELN 收件箱”“把收件箱整理进实验记录”之类时，按下面做。
目标：把用户随手记的口语/照片速记，整理成规范实验记录，**以“建议”提交**，由用户在浏览器 `/inbox` 逐条确认后才真正写入。**你只提交建议，绝不直接写入。**

本地接口：`http://127.0.0.1:8600`（本机访问免密码）。

## 步骤

1. 取待归档条目：
   `GET /api/inbox?status=pending`
   每条含：`id`、`text`（口语文字，可能空）、`image_urls`（图片直链数组）、`audio_url`、`hinted_experiment_id`（用户可能已标的实验，可空）、`created_at`。

2. 了解有哪些实验和步骤：
   `GET /api/experiment_summaries?status=active,needs_wrapup` → 实验列表（`id`、`name`、进度）。
   对可能相关的实验：`GET /api/experiments/{id}/steps` → 步骤（`id`、`step_index`、`title`、`description`、`fields`：每个含 `key`/`label`/`type`/`options`、`values` 当前值）。

3. 对每一条待归档条目判断归属：
   - 若有图片，读取 `image_urls`（形如 `http://127.0.0.1:8600/photos/inbox/<id>/<file>`）看清内容；也可直接读本机文件 `~/ELN_Data/photos/<image_path>`。
   - 判断它属于**哪个实验、哪一步**。`hinted_experiment_id` 有值时优先采用。
   - 写一段**规范中文备注**，忠实于用户所说，不要添加用户没说的结论。
   - **只有用户明确提到数值时**才填字段：匹配该步骤的 `field.key`；数值带单位时只填数字部分。绝不编造数字。
   - 如果明显不属于任何 active/needs_wrapup 实验，不要硬塞进旧实验。把这条作为“建议新建实验”处理：在 `reason` 里写清建议实验名、建议步骤、为什么现有实验不匹配；等用户确认后再新建。

4. 提交建议（**不要**调用 apply）：
   `POST /api/inbox/{id}/proposal`
   ```json
   {
     "experiment_id": 3,
     "step_id": 12,
     "note": "加样时观察到样品轻微浑浊",
     "fields": [{"key": "vol", "value": "12", "reason": "用户说加了大约12微升"}],
     "reason": "提到加样和浑浊，对应第1步加样"
   }
   ```
   实在无法归入任何实验的，可省略 `experiment_id`/`step_id`，在 `reason` 里说明，让用户手动处理。
   若是建议新建实验，也省略 `experiment_id`/`step_id`，并在 `reason` 里用“建议新建实验：...”开头。

5. 全部提交后，告诉用户共处理了几条，提示他到浏览器 `/inbox` 逐条确认（数字类字段请他核对）。

## 规则
- 一条速记最多一个建议；同一条的多点观察合并进一个 note。
- 忠实、可核对；证据不足就在 `reason` 里说明，不要硬编。
- 不要调用 `/api/inbox/{id}/apply` —— 写入由用户在浏览器确认。
- 不要未经用户确认就调用 `POST /api/experiments` 新建实验；用户确认新建后，再用最小可执行 protocol_json 建实验并把速记写进对应步骤。
