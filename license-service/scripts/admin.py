"""Local administration: python scripts/admin.py URL issue 5 permanent|30;
    python scripts/admin.py URL list|revoke UUID|reset UUID.
"""
import getpass
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = Path(__file__).resolve().parent.parent
def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    base, op = sys.argv[1].rstrip("/"), sys.argv[2]
    if not base.startswith("https://"):
        raise SystemExit("Для лицензирования нужен HTTPS URL Worker.")
    routes = {"issue":"/v1/admin/issue", "list":"/v1/admin/licenses",
              "revoke":"/v1/admin/revoke","reset":"/v1/admin/reset"}
    if op not in routes:
        raise SystemExit("Неизвестная операция")
    secret_file = root/"keys"/"admin_token.txt"
    secret = secret_file.read_text(encoding="utf-8").strip() if secret_file.exists() else getpass.getpass("Admin token: ")
    data = None
    if op == "issue":
        if len(sys.argv) < 5:
            raise SystemExit("Пример: issue 5 permanent либо issue 5 30")
        data = {"quantity":int(sys.argv[3]),
                "term_days": None if sys.argv[4]=="permanent" else int(sys.argv[4])}
    if op in ("revoke","reset"):
        if len(sys.argv) < 4:
            raise SystemExit("Укажите UUID лицензии")
        data = {"id":sys.argv[3]}
    request = Request(base+routes[op], method="GET" if op=="list" else "POST",
                      data=json.dumps(data).encode("utf-8") if data is not None else None,
                      headers={"Authorization":"Bearer "+secret,
                               "Content-Type":"application/json"})
    try:
        with urlopen(request,timeout=15) as result:
            response = json.load(result)
    except HTTPError as exc:
        raise SystemExit("Ошибка "+str(exc.code)+": "+exc.read().decode(errors="replace")) from exc
    print(json.dumps(response,ensure_ascii=False,indent=2))
    if op == "issue":
        print("ВАЖНО: коды появляются только один раз. Сохраните их.")

if __name__=="__main__":
    main()
