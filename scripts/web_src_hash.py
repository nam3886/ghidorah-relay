"""Hash of what the admin web bundle is built from (the app source, its entry page, public files, lockfile and the
build and TypeScript configs; not the tests, node_modules, generated declarations or the stamp itself).
web.sh build stores it in apps/admin-web/.bundle-stamp; check-in-container.sh refuses to run on a different one."""

import hashlib
import os
import sys

APP = "apps/admin-web"
TOP = ("src", "public", "index.html", "package.json", "package-lock.json", "vite.config.ts",
       "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "env.d.ts", ".npmrc")


def files(root: str):
    for top in TOP:
        path = os.path.join(root, top)
        if os.path.isfile(path):
            yield path
        for d, _dirs, names in os.walk(path):
            for n in names:
                yield os.path.join(d, n)


def main(root: str = APP) -> str:
    rows = []
    for p in files(root):
        with open(p, "rb") as fh:
            rows.append("%s %s\n" % (os.path.relpath(p, root), hashlib.sha256(fh.read()).hexdigest()))
    return hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()


if __name__ == "__main__":
    print(main(*sys.argv[1:]))
