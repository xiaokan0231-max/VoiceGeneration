# VoiceGeneration

ローカルで動作するプラグイン式のテキスト読み上げサービス。複数の TTS モデルの前面に統一された 1 つの REST API を配置し、音声クローン、プロジェクト、生成履歴、React 製の Web ワークベンチ、マルチマシンクラスタ、クロスプラットフォームのトレイアプリを備えています。

[English](README.md) · [简体中文](README.zh-CN.md) · **日本語**

VoiceGeneration は自分のマシン上で動作します（主に Apple Silicon、オプションで CUDA ワーカーノード）。複数のプラグイン式 TTS エンジン（CosyVoice3、F5-TTS、macOS の `say`）の前面に置かれる単一の REST API を公開し、zero-shot の音声クローンに対応し、生成物をプロジェクトとして整理し、履歴を MySQL に永続化し、ゲートウェイから直接配信される same-origin の React/TypeScript/Vite ワークベンチを同梱しています。最初は 歴史（歴史ドキュメンタリー）用途のために構築され、その後汎用化されました。

## Features

- **クロスプラットフォームのトレイアプリ** — 1 つのコードベースが macOS のメニューバーと Windows のシステムトレイの両方で動作します。起動時にノードのロール（coordinator / agent）を選び、バックエンドを監視し、停止した場合は自動再起動させられます。
- **マルチマシンクラスタ** — 1 台の Mac coordinator と、0 台以上のワーカー agent（例: Windows の RTX 4060）。すべてのオーディオ、履歴、ボイスは coordinator に集中して保持され、ノードはジョブをリースして結果を返送します。
- **プラグイン式モデル** — 各モデルは独自の conda env 内で隔離されたワーカーサブプロセスとして動作します。モデルの追加・削除は `models.yaml` を編集するだけで行え、ゲートウェイのコードには一切触れません。
- **Zero-shot 音声クローン** — 短い参照クリップから音声をクローンできます。クローンボイスはファイルベース（DB には保存されません）です。
- **Web ワークベンチ** — 生成、クローンボイスの管理、プロジェクトの整理、履歴の閲覧、サービス設定の調整をすべてゲートウェイから same-origin で配信します。
- **コンテンツアドレス指定のキャッシュ + 履歴** — 同一のリクエストは LRU ディスクキャッシュから即座に返され、すべての生成は MySQL に記録されます。
- **レプリカプール** — モデルごとに複数のワーカープロセスを動かして実際の並列性を得られます（単一ワーカーは内部的に直列です）。

## Architecture

**FastAPI ゲートウェイ**（conda env `vg-gateway`、Python 3.11）は唯一の外部エントリポイントです。REST API、`web/dist` からビルド済みの Web ワークベンチ、コンテンツアドレス指定のオーディオキャッシュ、MySQL（`voice_generation` DB）内の生成履歴 + プロジェクト、そしてファイルベースのボイスライブラリを配信します。

各モデルは依存関係の隔離のため、**独自の conda env** 内の**個別のワーカーサブプロセス**として動作します（例: 異なる torch バージョン）。スーパーバイザはワーカーごとに `<python> -m worker_runtime.server` を起動し、それが `workers/<model>/backend.py` をロードします。ワーカーは `/health`、`/info`、`/synthesize` を公開し、16-bit PCM WAV バイト列を返します。ゲートウェイは（ffmpeg で）トランスコードし結果を保存します。

```
client ──► gateway (FastAPI :8080) ──► supervisor ──► worker replica pool ──► backend
              │  REST API + web/dist                    (own conda env, lazy spawn,
              │  cache/ (LRU disk)                        idle-unload, ports base..base+N-1)
              │  MySQL: history + projects
              │  voices.yaml + voices/<id>/ref.wav
              └─ cluster queue (MySQL row locks)
                    ├─ embedded local agent (coordinator)
                    └─ remote agents (Windows / others, lease + post results)
```

- **レプリカプール** — 単一のワーカープロセスは直列（GIL + 単一の Metal/CUDA キュー）なので、実際の並列性は同一モデルに対して複数のプロセスを動かすことで得られます（`replicas=N`、各プロセスは自身のポート `base..base+N-1` 上で動作）。ジョブは `acquire()/release()` を通じて空きレプリカへディスパッチされます。
- **Lazy + idle** — ワーカーは最初のリクエストで起動し、`worker_idle_timeout` より長くアイドル状態が続くと回収されます。ヘルスチェックは `trust_env=False` を指定した `httpx` を使うため、VPN/TUN プロキシ（Clash/V2Ray）が `127.0.0.1` を傍受することはできません。
- **クラスタ** — 1 台の coordinator がキューを保持します。agent はジョブをリースし、推論を実行し、結果を返送します。coordinator は組み込みのローカル agent を動かすこともできます（`coordinator_runs_jobs`）。

[`gateway/main.py`](gateway/main.py)（全ルート）、[`gateway/supervisor.py`](gateway/supervisor.py)、[`gateway/cluster.py`](gateway/cluster.py)、[`gateway/agent.py`](gateway/agent.py) を参照してください。

## Quick start

### Prerequisites

- Apple Silicon（M シリーズ）上の macOS、[conda](https://docs.conda.io) 入り（スクリプトは `~/miniconda3` を想定）
- **MySQL**（Homebrew） — ゲートウェイは `mysql+pymysql://root@127.0.0.1:3306/voice_generation` に接続します
- **ffmpeg / ffprobe**（Homebrew） — オーディオのトランスコードと長さの取得に使用
- **Node.js + npm** — Web UI のビルド用

```bash
brew install mysql ffmpeg node
brew services start mysql
```

### Install

ワンショットのゲートウェイセットアップ: `vg-gateway` env（Python 3.11）を作成し、`models.example.yaml` → `models.yaml` をコピーし、依存関係をインストールし、Web UI をビルドし、DB マイグレーションを実行し、macOS トレイアプリをビルドします。

```bash
bash scripts/setup_gateway.sh
```

必要なモデルワーカーをセットアップし（それぞれが独自の conda env を持ちます）、必要に応じてウェイトをダウンロードします。

```bash
bash scripts/setup_worker.sh cosyvoice3      # creates env vg-cosyvoice, clones the official repo
bash scripts/download_weights.sh cosyvoice3  # downloads weights to models/Fun-CosyVoice3-0.5B-2512
bash scripts/setup_worker.sh f5_tts          # creates env vg-f5 (F5-TTS weights auto-download on first synth)
```

`setup_worker.sh` を実行した後は、`models.yaml` でそのモデルの `enabled: true` を設定し、ゲートウェイを再起動してください。

### Run — recommended: the tray app

macOS の `.app` をビルドし、起動してメニューからノードのロールを選びます。

```bash
bash scripts/build_macos_app.sh
open VoiceGeneration.app
```

トレイアプリはバックエンドを監視し（クラッシュ時に自動再起動）、ライブのステータスを表示し、開始/停止/再起動を切り替え、自動起動を設定し、終了します。

### Run — alternatives

```bash
bash scripts/start.sh            # foreground: alembic upgrade head, then uvicorn at http://127.0.0.1:8080
bash scripts/install_service.sh  # run the gateway as a background launchd service (no tray)
pkill -f "uvicorn gateway.main:app"   # stop a foreground/CLI gateway
```

ワークベンチを <http://127.0.0.1:8080> で開きます。CLI または API からの手早い合成は次のとおりです。

```bash
bash scripts/tts.sh "你好" cosyvoice3 narrator_zh out.wav

curl -s -X POST http://127.0.0.1:8080/v1/tts \
  -H 'content-type: application/json' \
  -d '{"text":"九一八事变后，东北局势急剧变化。","model":"cosyvoice3","voice":"narrator_zh","format":"wav"}' \
  -o out.wav
```

> **バックエンド**を変更した後はゲートウェイを再起動してください。**フロントエンド**を変更した後は `cd web && npm run build` を実行してリフレッシュしてください。ゲートウェイは `web/dist` をライブで配信するため、再起動は不要です。

## Models

[`models.example.yaml`](models.example.yaml) には 3 つのモデルが同梱されています。

| Model | Engine | Env | Device | Cloning | Modes | Notes |
|---|---|---|---|---|---|---|
| `system` | macOS `say` | gateway env | CPU | No | clone | ウェイトなし。組み込みボイス（Tingting / Kyoko / Samantha）。リンクテスト用にデフォルトで有効。非 macOS ノードでは `enabled: false` に設定してください。 |
| `cosyvoice3` | Fun-CosyVoice3-0.5B-2512 | `vg-cosyvoice` (py3.10) | auto/mps (Mac), cuda (Win) | Yes | clone, instruct, cross_lingual | レプリカあたり約 2.6 GB のメモリ。`options.repo_dir`、`options.model_dir`。ベースポート 8110、推奨 `replicas: 2`。 |
| `f5_tts` | F5TTS_v1_Base | `vg-f5` (py3.10) | auto (Mac), cuda (Win) | Yes | clone | ウェイトは初回合成時に HuggingFace から自動ダウンロード。ベースポート 8120、`replicas: 1`。 |

**生成モード**（`mode`、デフォルト `clone`）:

- `clone` — zero-shot の音声クローン（任意のクローン対応モデル）。
- `instruct` — 自然言語によるスタイル制御。**`cosyvoice3` のみ**。空でない `instruct_text` が必要です。
- `cross_lingual` — クロスリンガルのクローン。**`cosyvoice3` のみ**。

`clone` 以外のモードを `cosyvoice3` 以外のモデルに渡すと HTTP 400 が返ります。空の `instruct_text` で `instruct` を指定すると HTTP 400 が返ります。

> 実モデルへの最初の（コールド）呼び出しは遅く（ワーカー起動 + ウェイトロードを含めて約 25 秒）、両方の実モデルともリアルタイムより遅く動作します（RTF 約 1.5〜2.2）。バッチワークロードでは事前生成 + キャッシュを使用してください。macOS ではモデルを Docker 内で動かさ**ない**でください（MPS をパススルーできません）。conda env をまたいだネイティブプロセスを使用してください。

## Adding a model

モデルを追加するためにゲートウェイが変更されることはありません。プラグポイントは 2 つあります。

1. **`models.yaml` のエントリ** — `models:` の下に `id`、`python`（インタプリタパス。空 = ゲートウェイ自身）、`backend`（`module.path:ClassName`）、`host`、一意の `port`、`languages`、`supports_cloning`、`replicas`、そして `options` 辞書を持つブロックを追加します。
2. **`TTSBackend` サブクラス** — [`worker_runtime/base.py`](worker_runtime/base.py) の ABC をサブクラス化した `workers/<name>/backend.py` を書き、次を実装します。

   ```python
   def synthesize(self, req: SynthRequest) -> bytes:  # MUST return 16-bit PCM WAV bytes
       ...
   ```

   ウェイトは初回呼び出し時に遅延ロードしてください。`SynthRequest` は `text, voice, language, speed, mode, instruct_text, ref_audio_path, ref_text` を保持します。ヘルパー `pcm_to_wav_bytes(samples, sample_rate)` を使って、float `[-1,1]` または int16 の 1 次元配列をモノラルの 16-bit WAV にエンコードしてください。必要に応じて `list_voices() -> list[dict]` をオーバーライドして組み込みボイスを公開できます。

その後: conda env + 依存関係を作成し（[`scripts/setup_worker.sh`](scripts/setup_worker.sh) を参照）、YAML エントリを追加し、ゲートウェイを再起動します。モデルの削除 = その YAML ブロックを削除するだけです。

> `replicas=N` はポート `port..port+N-1` を占有するため、モデルのベースポート間にはギャップを空けてください。各ワーカーは自身に設定されたモデルのみを提供します。

## Voice cloning

クローンボイスはディスク上の `voices.yaml` + `voices/<id>/ref.wav` に存在します（DB には保存されません）。各ボイスは `id`、`name`、`language`、`ref_audio`、`ref_text`（参照音声の正確な文字起こし、一字一句）、そして `models`（空 = すべてのクローン対応モデル）を持ちます。

- クローン対応モデルはクローンボイスを**必要とします**。そのボイスはそのモデルで許可されている必要があり、その `ref_audio` ファイルが存在している必要があります（さもなければ HTTP 400）。
- ボイスは **音色库 / Voice Library** ページから管理します。アップロード（WAV/MP3/MP4/WebM、最大 20 MB、3〜30 秒）または Mac のマイクで直接録音できます。
- アップロードは **16 kHz モノラル WAV** に正規化されます。**先頭**の無音のみがトリムされます（F5-TTS が必要とするため末尾の無音は保持されます）。

> 参照音声の品質が重要です。文字起こしは音声と一字一句一致している必要があります。ノイズのない、単一話者の 6〜10 秒のクリップで末尾に約 1 秒の無音を入れることを推奨します。CosyVoice は約 30 秒で上限となり、F5-TTS は最初の約 12 秒のみを使用します。

## Projects

プロジェクトは MySQL の `projects` テーブルに保存される整理用のタグです。オーディオ出力には**影響せず**、**キャッシュキーから除外される**ため、同一のテキスト/モデル/ボイスはプロジェクト間で共有されます。プロジェクトを削除すると、その生成物は未割り当てになります（オーディオは削除されません）。履歴を絞り込むには `/v1/history?project=<id>`、未割り当てには `project=__none__` を使用します。

## Web workbench

[`web/`](web/) にある React 18 + TypeScript + Vite の SPA で、`web/dist` にビルドされ、ゲートウェイから same-origin（デフォルト <http://127.0.0.1:8080>）で配信されます。すべての API 呼び出しは same-origin の `/v1/*` パスを使用するため、CORS や別サーバは不要です。5 つのページがあります。

- **生成工作台 / Workbench**（`/`） — テキスト → 音声。左側にエディタ + 結果プレイヤー + 最近のリスト、右側に CONTROL パネル（プロジェクト、モデル、ボイス、モード、言語、フォーマット、速度、スタイル指示）。生成は非同期でクライアント側でポーリングされます。実行中のジョブは `localStorage` に永続化され、リロード後に再開されます。
- **音色库 / Voice Library**（`/voices`） — クローンボイスの作成・編集・削除・プレビュー。参照音声のアップロードまたは録音。
- **项目 / Projects**（`/projects`） — 生成物をプロジェクト（名前、説明、色）ごとにグループ化します。
- **生成历史 / History**（`/history`） — ページネーション・フィルタ可能な履歴（検索、モデル、ステータス、プロジェクト）。フィルタは URL に同期します。ワークベンチで生成物を再利用したり、プロジェクトへ移動したり、ダウンロードしたり、削除したりできます。
- **服务设置 / Settings**（`/settings`） — ヘルスカード（MySQL、キャッシュ、Apple MPS、ゲートウェイ）、グローバル設定、クラスタのランタイム + パフォーマンスメトリクス、サブノードの接続情報、モデルごとのサービス制御。

## Multi-machine cluster

1 台の **coordinator**（デフォルトでは Mac）が、ジョブキュー、オーディオキャッシュ、履歴、ボイスを MySQL 内に保持します。**ワーカーノード**（coordinator の組み込み agent + Windows RTX 4060 agent + …）はジョブをリースし、ローカルで推論を実行し、結果を返送します。ワーカーノードには MySQL も ffmpeg も不要です。トランスコードとストレージは coordinator 側で行われます。

- **ジョブのライフサイクル** — 各ジョブは `queued → leased → completed | failed`（および `cancelled`）と遷移する `generation_history` の行です。キューは MySQL の行ロックのみで実装されており、追加のブローカーはありません。
- **アトミックなリース** — ノードは `SELECT ... FOR UPDATE SKIP LOCKED` でワークを取得するため、2 つのノードが同じジョブを取ることはありません。リースは**モデルごと**（`{model_id: capacity}`）に行われるため、アイドルの `system`/F5 スロットがビジーな CosyVoice ジョブを過剰に取得することはありません。
- **フォールトトレランス** — リースには TTL（`lease_ttl`、デフォルト 120 秒）があります。期限切れのリースは（`max_attempts`、デフォルト 3 まで）再キューされるか失敗します。coordinator の再起動時には、残っているリース済みの行はすべて `queued` にリセットされます。遅いジョブはハートビートを送るため、重複として再ディスパッチされません。
- **重複排除** — 同じキャッシュキーを持つキュー済みの行は、すでに生成済みのオーディオを再利用します。
- **ノードの優先なし** — オンラインのすべてのノードが並列でキューを消化します。スループットはノードの合計です。モデルごとの実際の並列性 = 全ノードにわたるそのモデルのレプリカの合計です。
- **来歴** — HTTP レスポンスはオーディオを生成したマシン名を示す `X-Node` ヘッダを持ちます（キャッシュヒットの場合は coordinator の `node_id`）。履歴には「&lt;ノード名&gt; により生成」と表示されます。
- **リモート agent コンソール**（`:8090`） — サブノード自身の制御パネル（接続 / 切断 / 更新、キャパシティ、実行中のジョブ、coordinator のノードリスト）。agent はオペレータが Connect を押した**後にのみ**接続します（`cluster.enabled` はデフォルトで false で、状態は `models.yaml` に永続化されます）。
- **coordinator 専用モード** — `cluster.coordinator_runs_jobs: false` を設定すると、Mac は調整のみを行い、すべてのジョブが他のノードに送られます。

### Networking

ノードは **Tailscale**（安定した `100.x` IP）または同一 **LAN**（coordinator の LAN IP または `<hostname>.local`）で接続します。coordinator はデフォルトで `127.0.0.1` にバインドします。他のマシンが接続できるようにするには、`--host 0.0.0.0` で起動（または `settings.host: 0.0.0.0` を設定）し、**かつ** `cluster.token` を設定してください（両端で同一）。agent の `httpx` クライアントは `trust_env=False` を使ってシステムプロキシ/VPN をバイパスします。`curl http://<host>:8080/health` が 200 を返すことを確認してください。

```bash
# expose the coordinator to other machines
conda run -n vg-gateway uvicorn gateway.main:app --host 0.0.0.0 --port 8080

# start a worker node agent (console at http://127.0.0.1:8090)
bash scripts/agent.sh                                   # macOS / Linux
./scripts/agent.ps1 -CoordinatorUrl http://<host>:8080 -ClusterToken <token> -NodeId win-4060 -NodeName 'Windows 4060'   # Windows
```

[`docs/CLUSTER.md`](docs/CLUSTER.md) を参照してください。

## Tray app

1 つのコードベース（[`scripts/tray.py`](scripts/tray.py)、`pystray` + Pillow）が **macOS のメニューバー**と **Windows のシステムトレイ**の両方で動作します。

- **起動時のロール** — `models.yaml` の `cluster.role` から読み込みます。空の場合、メニューは *Start as coordinator / Start as agent* を提示し、選択された時点で初めて永続化して起動します。coordinator は uvicorn ゲートウェイ（`:8080`）を起動し、agent は `python -m gateway.agent`（`:8090` コンソール + リースループ）を起動します。
- **監視** — スレッドがバックエンドを生かし続けます。終了したが動作しているべき場合は、指数バックオフ（最大 30 秒）で自動再起動します。
- **ステータス** — 3 秒ごとにポーリングされます。coordinator は `running · nodes N · queue D` を表示し、agent は `connected/connecting/not-connected · running K/total slots` を表示します。アイコンはオンライン（銅色）とオフライン（グレー）を切り替えます。
- **メニュー** — ステータス · UI を開く · 停止/開始 · 再起動 · ロール切り替え · 自動起動 · 終了。トレイを終了すると**そのマシンの**バックエンドが停止します。
- **自動起動** — macOS は LaunchAgent の plist を使用し、Windows はレジストリの `Run` キーを使用します。
- **coordinator の起動** — macOS ではトレイは uvicorn を起動する前に `brew services start mysql`、続いて `alembic upgrade head` を実行します。

**Windows のパッケージングは委譲されています**。[`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md) を通じて Codex-on-Windows に委ねられます。契約: **`scripts/tray.py` をそのまま再利用する**こと（すでに Windows 分岐 — `CREATE_NEW_PROCESS_GROUP`、`winreg` 自動起動、トレイアイコン — を処理しています）と、クラスタプロトコルを変更**しない**こと。Windows の成果物には `scripts/start_tray.bat`、`install_autostart.ps1` / `uninstall_autostart.ps1`、そしてオプションの PyInstaller `VoiceGeneration.exe` が含まれます。

> PyInstaller に関する注意: フリーズされた exe は**リポジトリのルートを作業ディレクトリとして**実行する必要があります（さもなければ `models.yaml` とモデルの conda env を見つけられません）。`pythonw.exe scripts/tray.py` のショートカットは、文書化されたフリーズ不要のフォールバックです。いずれにせよリポジトリは各ワーカー上のディスクに残しておく必要があります。ワーカーは自身の conda env を使ってリポジトリディレクトリから起動します。

## REST API

ほとんどのユーザー向けルートは、`settings.api_token`（または `VG_API_TOKEN`）が設定されている場合にのみ `Authorization: Bearer <token>` を必要とします。クラスタルート（`/v1/cluster/*`）は別個の `cluster.token` を使用します。

> **セキュリティのデフォルト:** このサービスは**認証なし**で `127.0.0.1` にバインドされます。ローカル利用のみを想定しています。他のマシンに公開するのは、`settings.host: 0.0.0.0` を設定し、**かつ** `api_token` と `cluster.token` の両方を設定する場合のみにしてください。

| Method & path | Description |
|---|---|
| `GET /health` | ヘルスチェック `{ok, version}` |
| `POST /v1/tts` | 合成。オーディオ + `X-Generation-Id`、`X-Cache`（HIT\|MISS）、`X-Node` を返す |
| `POST /v1/generations` | 非同期生成を送信（200 キャッシュヒット、202 キュー投入） |
| `GET /v1/generations/{id}` | 生成のステータスをポーリング |
| `DELETE /v1/generations/{id}` | キュー済みの生成をキャンセル（リース済みなら 409） |
| `GET /v1/models` | 有効なモデルを一覧 |
| `GET /v1/voices?model=<id>` | ボイスを一覧（クローン + 組み込み）。設定のみで、ワーカーを起こすことはない |
| `GET /v1/voice-library` | すべてのクローンボイスを一覧 |
| `POST/PUT/DELETE /v1/voices[/{id}]` | クローンボイスの作成 / 更新 / 削除（multipart） |
| `GET /v1/voices/{id}/audio` | クローンボイスの参照 WAV をダウンロード |
| `GET /v1/history` | ページネーション/フィルタ付き履歴（`page, page_size, model, status, q, project`） |
| `PATCH /v1/history/{id}` | 生成のプロジェクトを再割り当て |
| `GET /v1/history/{id}/audio` | オーディオを取得（退避済みなら 410） |
| `DELETE /v1/history/{id}` | レコードを削除（ディスク上のオーディオは保持） |
| `GET/POST/PUT/DELETE /v1/projects[/{id}]` | プロジェクトを管理 |
| `GET /v1/settings` · `PUT /v1/settings` | グローバル設定 + モデルごとのランタイム状態。更新は `models.yaml` に書き込みホットリロード |
| `PUT /v1/models/{id}/config` | 1 つのモデルの設定を更新（ポート/パス/デバイスを検証） |
| `POST /v1/models/{id}/start\|stop\|restart` | モデルのレプリカプールをウォーム / 停止 / 再起動 |
| `GET /v1/system` | サービス/バージョン/プラットフォーム、MPS フラグ、MySQL の状態、キャッシュ使用量、モデル |
| `POST /v1/service/shutdown` | サービスをグレースフルに停止 |
| `POST /v1/cluster/register` | agent が登録（cluster-token 認証） |
| `POST /v1/cluster/lease` | agent がモデルごとのキャパシティでジョブをリースするためにロングポーリング |
| `GET /v1/cluster/asset/{voice_id}` | agent がクローン参照 WAV をダウンロード |
| `POST /v1/cluster/jobs/{id}/result\|fail\|heartbeat` | agent が結果をアップロード / 失敗を報告 / リースを延長 |
| `GET /v1/cluster/nodes` | クラスタ概要: 自身、ノード、`queue_depth` |
| `GET /v1/cluster/connect-info` | サブノードの接続情報（候補 URL は Tailscale 優先、トークン） |
| `GET /v1/jobs/{id}` | 単一のジョブ/履歴行を取得 |
| `GET /` · `GET /{path:path}` | Web SPA を配信（catch-all は最後に残す必要がある） |

> 新しい `GET` ルートは、[`gateway/main.py`](gateway/main.py) の最下部にある catch-all の `@app.get('/{path:path}')` SPA フォールバックより**前に**登録する**必要があります**。さもないとフォールバックがそれを飲み込んでしまいます。

## Configuration

`models.yaml` は **git-ignore** され、マシンごとです。[`models.example.yaml`](models.example.yaml) から始めてください（`cp models.example.yaml models.yaml`）。`PUT /v1/settings` と `PUT /v1/models/{id}/config` はそれをアトミックに書き換え、`models.yaml.bak` を保持します。

### Key settings

| Key | Default | Description |
|---|---|---|
| `settings.host` | `127.0.0.1` | バインドアドレス。他のマシンを受け入れるには `0.0.0.0` を設定 |
| `settings.port` | `8080` | ゲートウェイの HTTP ポート（Web UI も配信） |
| `settings.api_token` | `''` | 空でない場合、REST は `Bearer` を要求（env `VG_API_TOKEN`） |
| `settings.cache_dir` / `cache_max_gb` | `cache` / `3.0` | ディスクキャッシュのディレクトリ + サイズ上限（GB）。LRU で退避。設定のデフォルトは `3.0`、`models.example.yaml` は `30.0` を使用。 |
| `settings.worker_idle_timeout` / `worker_start_timeout` | `300` · `180` | アイドル回収 · 起動待ち（秒）。設定のデフォルトのアイドルは `300`、`models.example.yaml` は `3600` を使用。 |
| `settings.default_model` / `default_format` | `cosyvoice3` / `wav` | リクエストが省略したときのデフォルト |
| `settings.voices_file` | `voices.yaml` | クローンボイスのマニフェストパス |

### Cluster keys

| Key | Default | Description |
|---|---|---|
| `cluster.role` | `''` | `''`（トレイ起動時に選択）\| `coordinator` \| `agent` |
| `cluster.node_id` / `node_name` | `local` | 一意のノード id / 表示名 |
| `cluster.coordinator_url` | `''` | agent: coordinator の URL（coordinator では空のまま） |
| `cluster.token` | `''` | 共有のクラスタシークレット。coordinator を公開する際に必要 |
| `cluster.coordinator_runs_jobs` | `true` | coordinator も組み込み agent を通じて推論を実行 |
| `cluster.max_concurrency` | `1` | ノードの並列度ヒント（このノード自身の推論並列性） |
| `cluster.poll_interval` | `1.0` | agent のロングポーリング / アイドルスリープ間隔（秒） |
| `cluster.lease_ttl` / `node_timeout` / `max_attempts` | `120` / `60` / `3` | リース TTL · オフラインタイムアウト · 最大試行回数 |
| `cluster.agent_host` / `agent_port` | `127.0.0.1` / `8090` | サブノードの Web コンソールのバインド / ポート |
| `cluster.enabled` | `false` | agent が能動的に接続するかどうか（`:8090` コンソールで切り替え） |

### Per-model keys

`id`、`enabled`、`description`、`python`（インタプリタ。空 = ゲートウェイのもの）、`backend`（`module:Class`）、`host`、`port`（レプリカのベース）、`languages`、`supports_cloning`、`replicas`（並列プロセス、1〜8）、`options`（例: `device: auto|mps|cpu|cuda`、`repo_dir`、`model_dir`、`model`）、そして `placement.allow`（そのモデルの実行を許可するノード id のリスト。空 = すべて）。

### Environment overrides

`VG_API_TOKEN`、`VG_CLUSTER_ROLE`、`VG_NODE_ID`、`VG_NODE_NAME`、`VG_COORDINATOR_URL`、`VG_CLUSTER_TOKEN`、`VG_DATABASE_URL`、`VG_FFMPEG`、`VG_FFPROBE`。

> Windows ワーカーでは: 各モデルの `python` をその env の `python.exe` に向け、`options.device: cuda` を設定し（`torch.cuda.is_available()` を確認）、`model_dir`/`repo_dir` をローカルパスに向け、`system` モデルを `enabled: false` に設定してください。

## Data & cache

- **MySQL**（`voice_generation`。`VG_DATABASE_URL` で上書き可、デフォルト `mysql+pymysql://root@127.0.0.1:3306/voice_generation`）。3 つのテーブル: `generation_history`、`projects`、`cluster_nodes`。タイムスタンプは naive UTC として保存され、`Z` サフィックス付きでシリアライズされます。
- **Alembic** — `alembic upgrade head` は DB を自動作成してからマイグレーションを適用します（4 リビジョンの線形チェーン）。`init_database()` も `create_all` を実行するため、テーブルは ORM から現れることがあります。本番では Alembic を信頼できる情報源として維持してください。
- **コンテンツアドレス指定のディスクキャッシュ**（[`gateway/cache.py`](gateway/cache.py)） — オーディオは `cache/<key[:2]>/<key>.<ext>` の下に保存され、出力を決定するパラメータ（model、voice、language、text、speed、format、mode、instruct_text、options、ref、ref_text）の SHA-256 をキーとします。`project_id` と `assigned_node` は**除外される**ため、同一のコンテンツは共有され重複排除されます。`cache_max_gb` を超えた際の LRU 退避は `_logs` には決して触れません。
- **トランスコード**（[`gateway/audio.py`](gateway/audio.py)） — ワーカーは WAV を返し、ゲートウェイがオンデマンドでシステムの ffmpeg を通じて `wav | mp3 | opus` にトランスコードします。`ffmpeg`/`ffprobe` は `VG_FFMPEG`/`VG_FFPROBE`、`PATH`、続いて一般的な Homebrew のディレクトリの順に検出されます。
- **ファイルベースのボイス**（[`gateway/voice_store.py`](gateway/voice_store.py)） — `voices.yaml`（アトミック書き込み + `.bak`）と `voices/<id>/ref.wav`。ボイス ID は `^[A-Za-z0-9_-]{2,64}$` に一致します。

## Testing

バックエンドのテストには `root@127.0.0.1:3306`（パスワードなし）で稼働する MySQL が必要です。`tests/conftest.py` は `voice_generation_test` DB を強制するため、実行が実際の履歴に触れることはありません。

```bash
conda run -n vg-gateway python -m pytest -q   # backend
cd web && npm test                            # frontend (vitest)
```

> テストは `create_all` を使用します（既存テーブルに新しいカラムを追加しません）。ORM のカラムを変更した後は、再構築されるように影響を受けた `voice_generation_test` のテーブルを削除してください。

## Repository layout

```
gateway/            FastAPI gateway: main.py (all routes), supervisor.py, cluster.py,
                    agent.py, config.py, database.py, cache.py, audio.py, voice_store.py
worker_runtime/     worker server + TTSBackend ABC (base.py)
workers/<model>/    per-model backends (system, cosyvoice3, f5_tts)
web/                React + TS + Vite workbench (built to web/dist)
scripts/            setup/run/build scripts + tray.py + make_icons.py + agent.sh/ps1
packaging/          Info.plist, icons, .app launcher
                    (packaging/windows/ — PyInstaller spec — is a delegated Windows
                     deliverable, not built on the Mac side; see WINDOWS_AGENT_TASK.md)
alembic/            DB migrations
examples/           history_audio_router.py, batch_pregenerate.py (歴史 use case)
tests/              backend test suite
models.example.yaml model registry template (copy to models.yaml)
```

## Documentation

- [`docs/CLUSTER.md`](docs/CLUSTER.md) — マルチマシンクラスタのセットアップと運用
- [`docs/DESIGN.md`](docs/DESIGN.md) — アーキテクチャと設計ノート
- [`docs/WINDOWS_AGENT_TASK.md`](docs/WINDOWS_AGENT_TASK.md) — Windows agent/トレイのパッケージング契約
