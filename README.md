# Aurora Vectorizer API (Python)

Serviço HTTP que vetoriza logos (gradiente + multi-cor) em **linha para corte/gravação a laser**, com a mesma qualidade do motor Python aprovado: contornos suaves (potrace), texto multi-cor separado por matiz, e as dobras internas do emblema (gradiente) traçadas como linha única.

Feito pra rodar no **seu VPS** (não é serverless). Você sobe o serviço e sua aplicação chama por HTTP (`POST /vectorize`, multipart).

---

## Estrutura

```
aurora-vectorizer-api/
├── aurora_vectorizer.py   # motor (vetorização)
├── service.py             # API FastAPI (endpoints)
├── requirements.txt       # dependências Python
├── Dockerfile             # imagem (já instala o potrace)
├── docker-compose.yml     # subir com 1 comando
├── README.md              # este arquivo
└── exemplos/              # exemplo de entrada/saída
    ├── aurora.jpeg
    └── aurora.svg
```

---

## Deploy — Opção 1: EasyPanel (produção)

1. Crie um projeto **aurora-vectorizer** no EasyPanel.
2. Adicione um serviço **App** (Docker), apontando para o repo GitHub, branch `main`.
3. Build: Dockerfile (o existente funciona sem alteração). Porta: `8000`.
4. Env vars no EasyPanel:
   ```
   API_KEY=<secret-aleatorio>
   ```
5. Health check: `GET /health`, porta `8000`, intervalo 30s.
6. Restart policy: `always`.

O Traefik do EasyPanel cuida de SSL e reverse proxy. A URL fica algo como:
```
https://aurora-vectorizer-aurora-vectorizer.1nwz76.easypanel.host
```

### Autenticação

Se `API_KEY` estiver definida, toda chamada a `POST /vectorize` precisa do header:
```
X-API-Key: <mesma-key>
```
`GET /health` é aberto (sem auth). Se `API_KEY` não estiver definida, o serviço aceita tudo (modo dev).

---

## Deploy — Opção 2: Docker Compose (dev / VPS manual)

Pré-requisito: Docker e Docker Compose instalados.

```bash
docker compose up -d --build
```

Pronto. O serviço sobe em `http://localhost:8000`. Para ver logs / parar:

```bash
docker compose logs -f
docker compose down
```

Atualizar depois de mudar algo:

```bash
docker compose up -d --build
```

## Deploy — Opção 3: sem Docker (uvicorn + systemd)

```bash
# 1) instalar o potrace (binário) e o Python
sudo apt-get update && sudo apt-get install -y potrace python3-venv libglib2.0-0 libgl1

# 2) ambiente virtual + dependências
cd /opt/aurora-vectorizer-api          # ou onde colocou a pasta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3) testar rodando na mão
uvicorn service:app --host 0.0.0.0 --port 8000
```

Para deixar rodando como serviço (systemd), crie `/etc/systemd/system/aurora-vectorizer.service`:

```ini
[Unit]
Description=Aurora Vectorizer API
After=network.target

[Service]
WorkingDirectory=/opt/aurora-vectorizer-api
ExecStart=/opt/aurora-vectorizer-api/.venv/bin/uvicorn service:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aurora-vectorizer
sudo systemctl status aurora-vectorizer
```

---

## Testar

```bash
# saúde
curl http://localhost:8000/health        # -> {"ok": true}

# vetorizar (salva o SVG)
curl -F "file=@exemplos/aurora.jpeg" http://localhost:8000/vectorize -o saida.svg
```

---

## Endpoints

### `GET /health`
Retorna `{"ok": true}`.

### `POST /vectorize`  (multipart/form-data)
Recebe a imagem e devolve o **SVG** (`image/svg+xml`).

| campo (form) | tipo | default | o quê |
|---|---|---|---|
| `file` | arquivo | — | imagem do logo (png/jpg) — **obrigatório** |
| `saturation` | float | 1.8 | saturação antes de detectar as dobras (↑ realça o gradiente) |
| `stroke` | string | `#111111` | cor do traço |
| `stroke_width` | float | 2.4 | espessura do traço |
| `capture_folds` | bool | true | extrair as dobras internas do emblema |
| `color_separate_text` | bool | true | separar texto multi-cor por matiz (o "A" sai inteiro) |
| `fold_erode` | int | 11 | afasta as dobras da borda externa |
| `turdsize_text` | int | 8 | remove respingos no texto |
| `split_y` | int | -2 | corte emblema/texto: **-2 = automático** (maior vão), **-1 = sem texto**, **>=0 = manual** (px na imagem original) |

---

## Como sua aplicação chama

### Node / Next.js (fetch)

```ts
const fd = new FormData();
fd.append('file', new Blob([buffer]), 'logo.png');
// params opcionais:
// fd.append('saturation', '2.0'); fd.append('stroke', '#000000');

const r = await fetch(`${process.env.VECTORIZER_URL}/vectorize`, { method: 'POST', body: fd });
if (!r.ok) throw new Error('vectorizer falhou: ' + await r.text());
const svg = await r.text();   // string SVG pronta
```

Defina `VECTORIZER_URL` apontando pro VPS, ex.: `http://SEU_VPS:8000` (ou o domínio/IP interno).

### PHP / outra linguagem
É só um POST multipart com o campo `file`. O corpo da resposta é o SVG.

---

## Segurança (importante)

- O serviço **não tem autenticação** por padrão. Se o VPS for exposto à internet:
  - coloque atrás do seu **reverse proxy** (nginx/Caddy) e **não** abra a porta 8000 direto, ou
  - restrinja por firewall/rede interna, ou
  - adicione um header de API key no proxy.
- Ele só lê a imagem enviada e devolve SVG — não acessa disco do usuário nem banco.

## Notas de produção

- **potrace**: é o binário que gera os contornos suaves. O Dockerfile já instala; sem Docker, instale com `apt-get install potrace`.
- **Memória/tempo**: o motor trabalha em ~2x a imagem (qualidade). Logo de 1600px leva ~3–6s. Imagens muito grandes pedem mais RAM (o compose limita em 1 GB; ajuste se precisar).
- **Calibração por logo** (se algum logo pedir): `saturation` ↑ realça gradiente; `fold_erode` ajusta quão perto da borda as dobras vão; `split_y` força o corte emblema/texto se o automático errar.
