FROM codeberg.org/forgejo/forgejo:9
# pre-receive 腳本相依：decision.py 需 yaml；validation/__init__ 會 import numpy/scipy/pandas
# 不加 USER：Forgejo 官方映像預設即 root，入口程式依 compose 的 USER_UID/USER_GID 降權為 git（加 USER git 會讓容器起不來）。
RUN apk add --no-cache python3 py3-yaml py3-numpy py3-scipy py3-pandas
# 固定版本的 hook，與被推送的 repo 分離（spec §5.2）；升級走 PR + 手動 build。
COPY hooks/pre_receive.py /opt/gs-hooks/pre_receive.py
COPY hooks/pre-receive /opt/gs-hooks/pre-receive
# pinned-repo/ 由 README §3 的步驟以「已審核的 main commit」匯出（scripts/ 與 strategies/_common/validation/）
COPY pinned-repo/ /opt/gs-hooks/repo/
RUN chmod +x /opt/gs-hooks/pre-receive
