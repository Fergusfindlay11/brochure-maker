from pathlib import Path


def test_landing_project_open_uses_same_tab_anchor_navigation():
    html = Path("templates/landing.html").read_text(encoding="utf-8")

    assert 'href="/api/projects/${encodeURIComponent(p.id)}/brochure"' in html
    assert "window.open('/api/projects/${p.id}/brochure','_blank')" not in html
    assert "window.open(data.brochure_url, '_blank')" not in html
    assert "window.location.href = data.brochure_url;" in html
    assert "text-decoration: none;" in html
