"""
ELN App — Settings View
Server IP config, connection test, notification permissions, about.
"""

from __future__ import annotations
import platform
import flet as ft
from typing import Callable, Optional
from utils.app_settings import get_language, set_language
from utils.i18n import tr

def _open_overlay(page, ctrl):
    """Open a dialog/snackbar compatible with flet 0.70+."""
    if ctrl not in page.overlay:
        page.overlay.append(ctrl)
    ctrl.open = True
    page.update()

def _close_overlay(page, ctrl):
    """Close a dialog/snackbar compatible with flet 0.70+."""
    ctrl.open = False
    page.update()




def build_settings_view(
    page: ft.Page,
    is_mobile: bool = True,
    on_server_url_changed: Optional[Callable[[str], None]] = None,
    on_language_changed: Optional[Callable[[], None]] = None,
) -> ft.Control:

    import utils.api_client as api_client
    from server.startup import get_local_ip, is_server_running
    lang = get_language()
    _ = tr

    # ── Server IP (mobile only) ──────────────────
    current_url = api_client.get_base_url()
    tf_server_url = ft.TextField(
        value=current_url,
        label=_("服务器地址"),
        hint_text="http://192.168.1.100:8000",
        keyboard_type=ft.KeyboardType.URL,
        visible=is_mobile,
        border_color=ft.Colors.ORANGE_300,
        focused_border_color=ft.Colors.ORANGE_600,
    )

    conn_status = ft.Text("", size=13)

    def _test_connection(_):
        url = tf_server_url.value.strip()
        if not url:
            conn_status.value = _("请输入服务器地址")
            conn_status.color = ft.Colors.RED_400
            page.update()
            return

        api_client.set_base_url(url)
        conn_status.value = _("连接测试中…")
        conn_status.color = ft.Colors.GREY_500
        page.update()

        ok = api_client.check_connection()
        if ok:
            conn_status.value = _("✅ 连接成功")
            conn_status.color = ft.Colors.GREEN_600
            if on_server_url_changed:
                on_server_url_changed(url)
            page.client_storage.set("server_url", url)
        else:
            conn_status.value = _("❌ 连接失败，请检查 IP 和端口")
            conn_status.color = ft.Colors.RED_400
        page.update()

    def _save_url(_):
        url = tf_server_url.value.strip()
        if url:
            api_client.set_base_url(url)
            page.client_storage.set("server_url", url)
            if on_server_url_changed:
                on_server_url_changed(url)
            _open_overlay(page, ft.SnackBar(content=ft.Text(_("已保存"))))
            page.update()

    # ── Notification test (defined BEFORE it is referenced below) ────────────
    def _test_notification(_):
        from notifications import notify_timer_finished
        notify_timer_finished("测试步骤", "测试实验")
        _open_overlay(page, ft.SnackBar(content=ft.Text(_("通知已发送"))))
        page.update()

    def _change_language(e):
        set_language(e.control.value or "zh")
        if on_language_changed:
            on_language_changed()
        _open_overlay(
            page,
            ft.SnackBar(content=ft.Text(_("已切换语言，请刷新或切换页面查看效果"))),
        )

    # ── Local server info (desktop/Windows) ──────
    local_ip = ""
    server_running = False
    try:
        local_ip = get_local_ip()
        server_running = is_server_running()
    except Exception:
        pass

    server_info = ft.Container(
        content=ft.Column([
            ft.Text(_("本机服务器"), size=14, weight=ft.FontWeight.W_500),
            ft.Row([
                ft.Icon(
                    ft.Icons.CIRCLE,
                    size=10,
                    color=ft.Colors.GREEN_600 if server_running else ft.Colors.RED_400,
                ),
                ft.Text(
                    _("运行中") if server_running else _("未运行"),
                    size=13,
                    color=ft.Colors.GREEN_600 if server_running else ft.Colors.RED_400,
                ),
            ], spacing=6),
            ft.Text(f"{_('局域网地址：')}http://{local_ip}:8000",
                    size=13, color=ft.Colors.GREY_600,
                    selectable=True),
            ft.Text(_("在 iPhone 上输入此地址连接"),
                    size=12, color=ft.Colors.GREY_400),
        ], spacing=6),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
        visible=not is_mobile,
    )

    # ── Notification permission ───────────────────
    notif_section = ft.Container(
        content=ft.Column([
            ft.Text(_("通知设置"), size=14, weight=ft.FontWeight.W_500),
            ft.Text(_("计时结束时发送系统通知和提示音"),
                    size=12, color=ft.Colors.GREY_600),
            ft.ElevatedButton(
                _("测试通知"),
                on_click=_test_notification,
                bgcolor=ft.Colors.ORANGE_600,
                color=ft.Colors.WHITE,
                height=32,
            ),
        ], spacing=8),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
    )

    language_section = ft.Container(
        content=ft.Column([
            ft.Text(_("语言"), size=14, weight=ft.FontWeight.W_500),
            ft.Dropdown(
                value=lang,
                options=[
                    ft.dropdown.Option("zh", _("中文")),
                    ft.dropdown.Option("en", _("英文")),
                ],
                width=220,
                dense=True,
                on_select=_change_language,
            ),
        ], spacing=8),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
    )

    protocol_help_text = """用途
Protocol 是一个 JSON 模板，用来告诉 ELN App 一个实验有哪些步骤、每步要记录哪些数据、是否需要计时、是否需要拍照。把 protocol 保存到协议库后，可以从它新建实验。新建实验时，程序会把 protocol 复制成该实验的运行快照，所以之后编辑协议库不会自动改变已经创建的实验。

顶层结构
必须是一个 JSON object，推荐字段如下：
- protocol_name: 协议名称，必填，显示在协议库和新建实验流程里。
- version: 版本，可选，建议写字符串，例如 "1.0"。
- author: 作者，可选。
- steps: 步骤数组，必填，至少 1 个步骤。
- storage_items: 预设储存物品数组，可选。只有实验前已经知道要存什么时才写。实验结束后也可以临时添加本次实际要存的物品。

步骤 steps 的写法
每个 step 是一个 object：
- title: 步骤标题，必填。
- description: 步骤说明，必填。支持 Markdown（标题、列表、表格、加粗、代码块等）。需要换行时在 JSON 字符串里使用 \\n。
- timer_seconds: 计时秒数。0 或 null 表示没有计时器。30 分钟写 1800，2 小时写 7200。
- has_camera: true/false，表示该步骤原则上是否需要拍照/图片记录。附件上传区现在每一步都会显示。
- camera_required: true/false，表示照片是否原则上必需。即使必需，也允许先跳过，实验进入待收尾状态。
- fields: 记录字段数组。没有要填写的内容就写 []。

description 的 Markdown 写法
description 是 JSON 字符串，所以 Markdown 里的每一次换行都必须写成 \\n。
支持的常用格式：
- 标题：#、##、###
- 无序列表：- 项目
- 有序列表：1. 项目
- 表格：必须包含表头行、分隔行和内容行
- 加粗：**文字**
- 斜体：*文字*
- 行内代码：`文字`
- 代码块：三反引号

表格示例：
"description": "### 反应体系\\n| 组分 | 体积 |\\n|---|---:|\\n| 2x Mix | 10 µL |\\n| 模板 | 1 µL |\\n\\n- 冰上操作\\n- 轻轻混匀"

字段 fields 的写法
每个 field 是一个 object：
- key: 程序内部保存用的唯一英文 key。只能在同一步里唯一，建议用小写英文加下划线，例如 template_volume。
- label: 给人看的中文名称，例如 模板用量 (µL)。
- type: 字段类型，只支持 "text"、"number"、"dropdown"。
- default: 默认值，建议都写成字符串。没有默认值就写 ""。
- required: true/false，true 表示完成步骤前必须填写。
- options: 只有 dropdown 需要，写可选项数组；text 和 number 写 [] 即可。

预设储存物品 storage_items
如果实验前已经确定结束后要登记某些样品位置，可以在顶层写 storage_items。每个 item 是一个 object：
- key: 程序内部 key，英文或下划线，例如 pcr_product。
- label: 样品显示名称，例如 PCR 产物。
- tube_type: 管型，例如 1.5mL EP管、冻存管。
- default_box: 默认 Box 名称，可留空。当前只是记录提示，不会强制自动选中。
- notes_template: 默认备注，可留空。

实验结束临时添加储存物品
更推荐真实实验使用这个方式：做完实验后，程序会询问这次是否有东西要储存。输入格式是一行一个：
名称 | 管型 | 备注
例如：
PCR 产物 | 1.5mL EP管 | sample A
菌液甘油管 | 冻存管 | strain X
也可以只写名称：
PCR 产物
添加后，先选择 Box，再点击网格里的位置完成登记。

完整示例
{
  "protocol_name": "Colony PCR",
  "version": "1.0",
  "author": "Yanchang",
  "storage_items": [
    {
      "key": "pcr_product",
      "label": "PCR 产物",
      "tube_type": "1.5mL EP管",
      "default_box": "PCR_Box",
      "notes_template": "PCR product"
    }
  ],
  "steps": [
    {
      "title": "配制 PCR 反应体系",
      "description": "在冰上配制以下反应体系（总体积 20 µL）",
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
      "description": "将反应管放入 PCR 仪，运行 30 分钟",
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
      "description": "取 5 µL PCR 产物上样，100V 跑胶 30 分钟",
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

给 AI 生成 protocol 时可以这样要求
请把下面实验流程整理成 ELN App protocol JSON。必须输出合法 JSON，不要在 JSON 外面包 Markdown 代码块，也不要解释；但 description 字段内部可以使用 Markdown 文本。顶层包含 protocol_name、version、author、steps。每个 step 包含 title、description、timer_seconds、has_camera、camera_required、fields。description 如果有多段、列表或表格，请用 \\n 表示换行。fields 的 type 只能是 text、number、dropdown。若实验前已确定要储存的样品，加入 storage_items；若不确定，storage_items 写 []。

常见错误
- 不要在 JSON 里写注释。
- true/false 要小写，不要写 True/False。
- 字符串必须用双引号。
- description 里的 Markdown 换行必须写成 \\n，不能在 JSON 字符串里直接硬回车。
- timer_seconds 必须是秒数，不是分钟数。
- dropdown 必须提供 options。
- required 只控制能否完成步骤，不会自动生成默认值。
- 已经创建的实验可以在执行页单独修改实验名、步骤标题、步骤说明和记录字段，不会改协议库模板。
"""
    if lang == "en":
        protocol_help_text = """Purpose
A protocol is a JSON template that tells ELN App what steps an experiment has, what data to record in each step, whether a timer is needed, and whether photos are needed. When you create an experiment from a protocol, ELN copies the protocol into that experiment as a runtime snapshot, so later edits to the protocol library do not change old experiments.

Top-level JSON
The top level must be a JSON object. Recommended fields:
- protocol_name: required. The protocol name shown in the library and new-experiment flow.
- version: optional string, for example "1.0".
- author: optional string.
- steps: required array with at least one step.
- storage_items: optional array for items already known before the experiment starts. If you do not know what will be stored until the end, use [] and add storage items during wrap-up.

Step fields
Each step is an object:
- title: required step title.
- description: required step instructions. Markdown is supported, including headings, lists, tables, bold text, and code blocks. Use \\n inside the JSON string for line breaks.
- timer_seconds: timer length in seconds. Use 0 or null for no timer. 30 minutes is 1800; 2 hours is 7200.
- has_camera: true/false. Marks whether this step is expected to need photo/image evidence. The attachment upload section is shown on every step.
- camera_required: true/false. Required photos can still be skipped temporarily; the experiment becomes wrap-up until photos are completed.
- fields: array of record fields. Use [] if no manual records are needed.

Markdown in description
description is a JSON string, so every Markdown line break must be written as \\n.
Common supported formats:
- Headings: #, ##, ###
- Unordered lists: - item
- Ordered lists: 1. item
- Tables: header row, separator row, then body rows
- Bold: **text**
- Italic: *text*
- Inline code: `text`
- Code blocks: triple backticks

Table example:
"description": "### Reaction mix\\n| Component | Volume |\\n|---|---:|\\n| 2x Mix | 10 uL |\\n| Template | 1 uL |\\n\\n- Keep on ice\\n- Mix gently"

Record fields
Each field is an object:
- key: unique machine key within this step. Use lowercase English and underscores, for example template_volume.
- label: human label, for example Template volume (uL).
- type: one of "text", "number", "dropdown".
- default: default value as a string. Use "" for blank.
- required: true/false. Required fields must be filled before completing the step.
- options: required only for dropdown fields.

Storage items
If storage items are known before the run, add storage_items at the top level:
- key: machine key, for example pcr_product.
- label: display name.
- tube_type: tube/container type.
- default_box: suggested Box name. This does not create or auto-select a Box.
- notes_template: optional default note.

End-of-experiment storage
Most real experiments should add storage items at the end. Use one item per line:
sample name | tube type | notes

Example:
PCR product | 1.5 mL tube | sample A
Glycerol stock | cryotube | strain X

You may also enter only the sample name. After adding items, choose a Box and click a grid slot.

Prompt for AI
Convert the following protocol into ELN App protocol JSON. Output valid JSON only, with no explanation and no Markdown wrapper outside the JSON. Markdown is allowed inside description strings. Use \\n for multi-paragraph descriptions, lists, and tables. Each step must include title, description, timer_seconds, has_camera, camera_required, and fields.

Common mistakes
- JSON cannot contain comments.
- Use lowercase true/false, not True/False.
- Strings must use double quotes.
- Markdown line breaks inside description must be written as \\n, not literal line breaks inside the JSON string.
- timer_seconds is seconds, not minutes.
- dropdown fields must provide options.
- required only controls step completion; it does not generate default values.
"""

    protocol_help = ft.Container(
        content=ft.Column([
            ft.Text(_("Protocol 语法帮助"), size=14, weight=ft.FontWeight.W_500),
            ft.Text(
                protocol_help_text,
                size=12,
                color=ft.Colors.GREY_700,
                selectable=True,
            ),
        ], spacing=8),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
    )

    # ── About ────────────────────────────────────
    about_section = ft.Container(
        content=ft.Column([
            ft.Text(_("关于"), size=14, weight=ft.FontWeight.W_500),
            ft.Text(_("ELN App — 个人实验室笔记"), size=13),
            ft.Text(f"{_('平台：')}{platform.system()} {platform.machine()}",
                    size=12, color=ft.Colors.GREY_500),
            ft.Text(_("数据存储：SQLite（本地）"),
                    size=12, color=ft.Colors.GREY_500),
        ], spacing=4),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
    )

    # ── Mobile: server URL section ────────────────
    mobile_server_section = ft.Container(
        content=ft.Column([
            ft.Text(_("服务器连接"), size=14, weight=ft.FontWeight.W_500),
            tf_server_url,
            ft.Row([
                ft.ElevatedButton(
                    _("测试连接"),
                    on_click=_test_connection,
                    bgcolor=ft.Colors.ORANGE_600,
                    color=ft.Colors.WHITE,
                    height=32,
                ),
                ft.OutlinedButton(
                    _("保存"),
                    on_click=_save_url,
                    height=32,
                ),
            ], spacing=8),
            conn_status,
        ], spacing=8),
        border=ft.Border.all(1, ft.Colors.GREY_200),
        border_radius=8,
        padding=12,
        visible=is_mobile,
    )

    header = ft.Container(
        content=ft.Text(_("设置"), size=20, weight=ft.FontWeight.BOLD),
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
    )

    return ft.Column([
        ft.Divider(height=1, color=ft.Colors.GREY_200),
        header,
        ft.Container(
            content=ft.Column([
                mobile_server_section,
                server_info,
                language_section,
                notif_section,
                protocol_help,
                about_section,
            ], spacing=12, scroll=ft.ScrollMode.AUTO),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            expand=True,
        ),
    ], expand=True, spacing=0)
