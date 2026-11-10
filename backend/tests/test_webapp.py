from fastapi.testclient import TestClient

from cyberlab.main import create_app


def built(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(
        '<!doctype html><script type="module" src="/app/assets/a.js"></script><div id=root></div>'
    )
    (d / "assets" / "a.js").write_text("console.log('cyberlab')")
    return d


def test_serves_built_app_with_strict_csp(settings, tmp_path):
    app = create_app(settings.model_copy(update={"webapp_dir": built(tmp_path)}))
    with TestClient(app) as c:
        r = c.get("/app/")
        assert r.status_code == 200 and "id=root" in r.text
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-eval" not in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
        assert "frame-ancestors 'none'" in csp
        js = c.get("/app/assets/a.js")
        assert js.status_code == 200 and "cyberlab" in js.text
        assert c.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_explains_when_not_built(settings, tmp_path):
    app = create_app(settings.model_copy(update={"webapp_dir": tmp_path / "missing"}))
    with TestClient(app) as c:
        r = c.get("/app/")
        assert r.status_code == 503 and "npm run build" in r.text
        assert c.get("/app/workspace/x").status_code == 503


def test_app_pages_dont_use_up_the_rate_limit(settings, tmp_path):
    app = create_app(
        settings.model_copy(
            update={
                "webapp_dir": built(tmp_path),
                "rate_limit_per_minute": 60,
                "rate_limit_burst": 2,
            }
        )
    )
    with TestClient(app) as c:
        assert all(c.get("/app/assets/a.js").status_code == 200 for _ in range(10))
        assert c.get("/v1/registry/categories").status_code == 200
