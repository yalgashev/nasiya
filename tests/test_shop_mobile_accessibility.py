from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.shop.enums import ShopRole

CSS_PATH = Path("app/static/css/app.css")
TEMPLATES_DIR = Path("app/templates")
SHOP_TEMPLATE_NAMES = (
    "shop/workspace.html",
    "shop/select.html",
    "shop/staff.html",
)


class HtmlAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1_count = 0
        self.labels_for: set[str] = set()
        self.control_ids: set[str] = set()
        self.visible_text_parts: list[str] = []
        self.inline_style_count = 0
        self.script_count = 0
        self._hidden_input_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_map = {name: value for name, value in attrs}
        if tag == "h1":
            self.h1_count += 1
        if tag == "script":
            self.script_count += 1
        if "style" in attr_map:
            self.inline_style_count += 1
        if tag == "label":
            label_target = attr_map.get("for")
            if label_target:
                self.labels_for.add(label_target)
        if tag in {"input", "select", "textarea"}:
            control_id = attr_map.get("id")
            control_type = (attr_map.get("type") or "").casefold()
            if control_id and control_type != "hidden":
                self.control_ids.add(control_id)
        if tag == "input" and (attr_map.get("type") or "").casefold() == "hidden":
            self._hidden_input_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "input" and self._hidden_input_depth:
            self._hidden_input_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.visible_text_parts.append(text)

    @property
    def visible_text(self) -> str:
        return " ".join(self.visible_text_parts)


def render_template(template_name: str, **context: object) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(
            enabled_extensions=("html", "xml"),
            default=True,
        ),
    )
    environment.globals["url_for"] = static_url_for
    return environment.get_template(template_name).render(**context)


def static_url_for(name: str, **params: str) -> str:
    assert name == "static"
    return f"/static/{params['path']}"


def parse_html(rendered: str) -> HtmlAuditParser:
    parser = HtmlAuditParser()
    parser.feed(rendered)
    return parser


def render_shop_templates() -> dict[str, str]:
    return {
        "shop/workspace.html": render_template(
            "shop/workspace.html",
            shop_name="Mobile Shop",
            status_label="to'xtatilgan",
            role_label="egasi",
            active_staff_count=2,
            is_read_only=True,
            show_shop_switcher=True,
        ),
        "shop/select.html": render_template(
            "shop/select.html",
            csrf_token="csrf-token",
            has_memberships=True,
            memberships=[
                {
                    "shop_id": "11111111-1111-4111-8111-111111111111",
                    "shop_name": "Active Shop",
                    "status_label": "faol",
                },
                {
                    "shop_id": "22222222-2222-4222-8222-222222222222",
                    "shop_name": "Suspended Shop",
                    "status_label": "to'xtatilgan",
                },
            ],
        ),
        "shop/staff.html": render_template(
            "shop/staff.html",
            csrf_token="csrf-token",
            shop_name="Staff Shop",
            error_message="Xodimni qo'shib bo'lmadi.",
            notice_message="Xodim saqlandi.",
            is_read_only=True,
            can_manage_staff=True,
            has_staff=True,
            role_options=[
                {"value": role.value, "label": role.value}
                for role in (
                    ShopRole.OWNER,
                    ShopRole.MANAGER,
                    ShopRole.CASHIER,
                )
            ],
            staff_rows=[
                {
                    "staff_id": "33333333-3333-4333-8333-333333333333",
                    "masked_phone": "+998*******01",
                    "role": ShopRole.OWNER,
                    "role_label": "egasi",
                    "created_at": "2026-07-27 18:00 UTC",
                }
            ],
        ),
    }


def test_shop_css_has_mobile_first_320_430_and_44px_touch_rules() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")

    assert ".shop-workspace" in css
    assert ".shop-select" in css
    assert ".shop-staff" in css
    assert "max-width: 100%;" in css
    assert "overflow-wrap: anywhere;" in css
    assert ".shop-staff select" in css
    assert ".shop-actions a" in css
    assert "min-height: 44px;" in css
    assert ":focus-visible" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert "width: min(100% - 24px, 640px);" in css
    assert "width: min(100% - 16px, 640px);" in css
    assert "100vw" not in css
    assert "min-width: 44px;" in css
    assert "min-width: 320px" not in css
    assert "min-width: 430px" not in css
    assert "white-space: nowrap" not in css
    assert "overflow-x: scroll" not in css
    assert "overflow-x: auto" not in css
    assert "@import" not in css
    assert "@font-face" not in css
    assert "font-family:" in css
    assert "animation:" not in css
    assert "@keyframes" not in css


def test_shop_templates_have_one_h1_labels_text_status_and_messages() -> None:
    rendered_by_template = render_shop_templates()

    for template_name, rendered in rendered_by_template.items():
        parser = parse_html(rendered)
        assert parser.h1_count == 1, template_name
        assert parser.control_ids <= parser.labels_for, template_name
        assert parser.inline_style_count == 0, template_name
        assert parser.script_count == 0, template_name
        assert "<style" not in rendered.casefold()
        assert " style=" not in rendered.casefold()

    workspace_text = parse_html(
        rendered_by_template["shop/workspace.html"]
    ).visible_text
    select_text = parse_html(rendered_by_template["shop/select.html"]).visible_text
    staff_text = parse_html(rendered_by_template["shop/staff.html"]).visible_text

    assert "Holat to'xtatilgan" in workspace_text
    assert "faqat ko'rish rejimi" in workspace_text
    assert "Holat: faol" in select_text
    assert "Holat: to'xtatilgan" in select_text
    assert "Xodimni qo'shib bo'lmadi." in staff_text
    assert "Xodim saqlandi." in staff_text
    assert "faqat ko'rish rejimi" in staff_text


def test_shop_templates_keep_mobile_css_link_without_new_dependencies() -> None:
    for template_name, rendered in render_shop_templates().items():
        assert 'href="/static/css/app.css"' in rendered, template_name
        assert "bootstrap" not in rendered.casefold()
        assert "tailwind" not in rendered.casefold()
        assert "fontawesome" not in rendered.casefold()
        assert "lucide" not in rendered.casefold()
