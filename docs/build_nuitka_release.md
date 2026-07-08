# Build Nuitka + release do Demo Viewer (IA Pediatria)

Gera um `.exe` standalone (one-dir, via Nuitka) do Demo Viewer e empacota
uma pasta pronta para zipar/entregar ao cliente. Não compila o serviço
headless (`tools/run_pediatria_service_api.py`) nesta rodada — só a UI de
demonstração (`tools/run_pediatria_viewer.py`), que é o artefato visível
para o cliente/marketplace.

## Pré-requisitos

- Windows 10/11 64-bit.
- Venv do projeto já criado e com `requirements-mvp.txt` instalado
  (`setup_mvp.ps1`).
- ~15 GB livres em disco (o build intermediário do Nuitka + a pasta `dist`
  final somam bastante, principalmente por causa do `torch`, ver
  "Por que o build é grande" abaixo).
- Compilador C: o Nuitka baixa um MinGW64 portátil automaticamente no
  primeiro build (por isso o script usa `--assume-yes-for-downloads`); se a
  máquina já tiver MSVC (`cl.exe`) no PATH, o Nuitka usa esse em vez disso.
- Nuitka instalado no venv do projeto:
  ```powershell
  .\.venv\Scripts\python.exe -m pip install nuitka ordered-set zstandard
  ```

## Como rodar

```powershell
# so compila (gera build\nuitka\run_pediatria_viewer.dist\)
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1

# compila e ja monta a pasta de release + ZIP
powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -Package

# so empacota (reaproveitando um build já feito)
.\.venv\Scripts\python.exe scripts\package_release.py --version 20260708
```

Parâmetros do `build_nuitka_windows.ps1`:

| Parâmetro  | Default          | O que faz                                              |
|------------|------------------|---------------------------------------------------------|
| `-Version` | data de hoje (`yyyyMMdd`) | Rótulo usado no relatório e em `BUILD_INFO.json`. |
| `-Clean`   | desligado        | Apaga `build\nuitka` antes de compilar (build limpo).   |
| `-Package` | desligado        | Chama `scripts\package_release.py` automaticamente após o build. |

Parâmetros do `package_release.py`:

| Parâmetro  | Default          | O que faz                                              |
|------------|------------------|---------------------------------------------------------|
| `--version`| data de hoje     | Nome da pasta/ZIP: `release\IA_Pediatria_<version>`.    |
| `--force`  | desligado        | Sobrescreve a pasta de release se ela já existir.       |

## O que o build inclui (e por quê)

- `--standalone` (one-dir), **nunca** `--onefile` nesta etapa: PyQt6 +
  OpenCV + Ultralytics/OpenVINO + a COM do `pyttsx3`/`comtypes` (voz) são
  sensíveis à extração temporária que o modo onefile faz a cada execução.
- `--enable-plugins=pyqt6`: plugin oficial do Nuitka para o binding Qt
  realmente usado neste projeto (confirmado em `requirements-mvp.txt` e via
  `from PyQt6 import QtCore`; **não é chute**).
- `--include-package-data=openvino` **+** `--include-data-dir` explícito de
  `openvino\libs`: o pacote `openvino` resolve suas DLLs nativas
  (`openvino.dll`, plugin de CPU, TBB, etc.) via
  `os.path.dirname(__file__) + "libs"` em runtime (não é um import estático
  nem um "data file" no sentido do Nuitka — são carregadas por nome via
  `plugins.xml`). Sem isso a inferência falha *só dentro do binário
  congelado*, nunca em `python tools\run_pediatria_viewer.py` — é o erro
  mais comum e mais silencioso ao empacotar OpenVINO com Nuitka.
- `--include-package-data=ultralytics`: a lib carrega configs `.yaml`
  internos (ex.: `bytetrack.yaml`, usado pelo tracker) como arquivo, não
  como módulo Python.
- `--include-package=comtypes`: geração de wrapper COM do `pyttsx3`
  (driver SAPI5 no Windows) pode não ser descoberta por análise estática.
- `--include-package=src` / `--include-package=modulo`: o entrypoint faz
  `sys.path.insert(0, ROOT)` e importa com `from src.jutta_ped...` /
  `from modulo.pediatria...`; forçar a inclusão evita depender de o Nuitka
  adivinhar isso pela manipulação de `sys.path` em runtime.
- **Modelos (`.pt`/OpenVINO) não entram no build nem no release.** Ficam
  100% fora do binário e fora do ZIP — `package_release.py` só cria o
  esqueleto vazio (`runtime\models\`, `runtime\model_registry.example.json`).
  O técnico importa o modelo real depois de instalado, pela tela escondida
  do Demo Viewer (ver "Modelos externos e o Gerenciador de Modelos"
  abaixo). Isso preserva o carregamento dinâmico por configuração — trocar
  de modelo não exige recompilar nada, e reduz o tamanho do ZIP de entrega.

## Por que o build é grande

`ultralytics` importa `torch` incondicionalmente no seu próprio
`__init__.py` (não é código deste projeto, é a dependência em si) — então
`torch` **não pode ser excluído** do build sem quebrar o carregamento do
modelo, mesmo a inferência de produção rodando 100% em OpenVINO/CPU. O
venv atual usa `torch==2.5.1+cu121` (build com CUDA, ~4.3 GB só o pacote),
que domina o tamanho final.

**Recomendação para builds de release** (não aplicada automaticamente por
este script, é uma escolha deliberada de ambiente): reinstalar o venv com
torch CPU-only antes de buildar:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mvp.ps1 -CpuOnly
```

Isso reduz o pacote `torch` de ~4.3 GB para algumas centenas de MB. A
detecção automática de GPU CUDA (`--device auto`) passa a sempre resolver
para CPU nesse caso — o pipeline padrão (OpenVINO/CPU, que é o usado por
default pelos modelos deste repo) não é afetado. Fica registrado em
`BUILD_INFO.json` (`notes`) qual venv gerou cada build.

## Estrutura da pasta de release

```
release/IA_Pediatria_<version>/
  IA_Pediatria_DemoViewer.exe
  <suporte gerado pelo Nuitka: DLLs, .pyd, python3XX.dll, etc.>
  runtime/
    models/
      README_MODELS.txt            (comeca vazio -- sem peso de modelo nenhum)
    model_registry.example.json    (so referencia de schema, nao e lido pelo app)
  pediatria_results/
    README_RESULTADOS.txt          (pasta comeca vazia; evidencias/eventos
                                     de cada sessao vao aqui)
  Iniciar_Demo.bat                  (launcher: so passa --report; o app
                                     resolve o modelo sozinho via registry)
  README_CLIENTE.txt
  BUILD_INFO.json
  CHECKSUMS.sha256
```

`runtime\model_registry.json` (o arquivo real, diferente do `.example.json`
acima) só aparece depois que o técnico importa o primeiro modelo pela tela
escondida — não é criado pelo empacotamento.

Este projeto não tem pastas `assets/`, `sounds/` ou `logs/` separadas (voz é
sintetizada via `pyttsx3`, sem arquivos de áudio; toda saída em disco vai
para `pediatria_results/`) — por isso a estrutura acima é mais enxuta que um
template genérico de release.

## Modelos externos e o Gerenciador de Modelos

Desde esta rodada, nenhum peso de modelo (`.pt`, `.onnx`, `.xml`, `.bin`)
entra no build nem no ZIP de entrega. O app abre normalmente sem nenhum
modelo configurado — só ao clicar em "Iniciar" sem modelo válido aparece um
aviso explicando o que falta (não é um crash).

**Abrir a tela escondida:** com o Demo Viewer aberto, pressione
`Ctrl+Shift+M`. Abre o "Gerenciador de Modelos" — não tem nenhum botão nem
menu visível na UI principal que leve até lá de propósito (é uma tela
técnica, não para o usuário final).

**Importar um modelo:**
- **`.pt`** (arquivo único, formato nativo do PyTorch/Ultralytics): botão
  "Importar .pt", escolha o arquivo, informe papel (`specialist` = classifica
  adult/child; `detector` = detector genérico de pessoa), nome e versão. O
  arquivo é copiado para `runtime\models\` — o original não é movido nem
  apagado.
- **OpenVINO** (`.xml` + `.bin`, exportado do Ultralytics): botão "Importar
  OpenVINO (.xml)", escolha o `.xml` — o `.bin` de mesmo nome na mesma pasta
  é exigido e copiado junto automaticamente (e `metadata.yaml`, se existir
  ao lado, também é copiado por completude, mas não é obrigatório).
- Nenhuma importação apaga modelos já importados antes — eles continuam
  listados na tabela, só não ficam "ativos" a menos que sejam selecionados.

**Trocar a versão ativa:** selecione a linha na tabela (mostra papel, nome,
versão, backend e se está ativo) e clique em "Definir como ativo". Vale a
partir do próximo clique em "Iniciar" no Demo Viewer; no serviço headless
(`tools/run_pediatria_service_api.py`), vale a partir do próximo restart do
processo (o caminho é resolvido uma vez, na subida do serviço).

**Rollback:** não é um botão separado — é a mesma ação de "Definir como
ativo", só que escolhendo uma entrada mais antiga da tabela (que continua
lá, porque importar nunca apaga o que já existia). `runtime\model_registry.json`
também recebe um backup automático (`.json.bak`) antes de cada gravação.

**Testar antes de usar em produção:** botão "Testar carregamento" tenta
carregar o modelo de verdade (mesmo caminho de código usado na análise real,
`PediatricsDetectorMvpRunner`) e mostra sucesso/erro — sem precisar iniciar
uma análise de vídeo para descobrir se o arquivo está corrompido ou é
incompatível.

**Diferença `.pt` vs. OpenVINO:** `.pt` é o formato nativo do PyTorch,
carrega em qualquer device (`cpu`/`cuda:0`) mas depende do `torch` completo
estar disponível. OpenVINO (`.xml`+`.bin`) é o formato exportado/otimizado
para CPU Intel — é o que os modelos padrão deste repo usam por padrão hoje
(mais rápido em CPU, não depende de CUDA). O código já trata os dois formatos
de forma transparente (`PediatricsDetectorMvpRunner._default_model_loader`);
o Gerenciador de Modelos não faz conversão entre um formato e outro — importa
o que já foi exportado antes, fora desta ferramenta.

## Smoke test

`package_release.py` já valida, antes de gerar o ZIP, que:

- `IA_Pediatria_DemoViewer.exe` existe na pasta copiada do dist do Nuitka;
- nenhum arquivo da lista proibida (`.env`, `.git`, `*.pem`, `*.key`,
  `__pycache__`, `.pytest_cache`, `.mypy_cache`, vídeos de teste, etc.)
  sobrou na pasta final;
- nenhum peso de modelo (`*.pt`, `*.onnx`, `*.xml`, `*.bin`) vazou para
  dentro do release (falha o empacotamento se encontrar qualquer um dos
  dois casos acima).

Validação manual recomendada após o build (não abre pipeline RTSP real
nem trava a sessão):

```powershell
cd release\IA_Pediatria_<version>
.\IA_Pediatria_DemoViewer.exe
```

A janela deve abrir sem crash imediato (tela de login: `admin`/`admin`,
depois "Acesso Rápido (Demo)"), **mesmo sem nenhum modelo configurado** —
isso é o comportamento esperado agora (modelos são externos). Feche a
janela — isso já valida que PyQt6 + OpenCV + Ultralytics + OpenVINO +
comtypes carregaram corretamente dentro do binário congelado. Clicar em
"Iniciar" sem modelo configurado deve mostrar um aviso explicando o que
falta, não travar nem fechar a janela sozinha.

## Troubleshooting

| Sintoma | Causa provável | Solução |
|---|---|---|
| Nuitka trava pedindo para baixar algo | Falta `--assume-yes-for-downloads` (já está no script) ou proxy/firewall bloqueando `github.com` | Rodar uma vez com rede livre; o MinGW64 baixado fica em cache do Nuitka (`%NUITKA_CACHE_DIR%` ou `%LOCALAPPDATA%\Nuitka\Nuitka\Cache`) |
| App abre e fecha sem erro visível, ou trava ao carregar modelo | DLLs do OpenVINO não foram encontradas dentro do binário | Confirmar que `release\...\openvino\libs\*.dll` existe; se não existir, o `--include-data-dir` do `openvino\libs` falhou -- confira se `.venv\Lib\site-packages\openvino\libs` existe antes de buildar |
| Sem voz / erro de COM ao iniciar alerta | `comtypes` não gerou o wrapper SAPI5 dentro do build congelado | Testar com `--voice=False` no `Iniciar_Demo.bat` como contorno; reportar para revisão de packaging do `comtypes` |
| Build absurdamente grande (>5 GB) | `torch+cu121` (CUDA) sendo empacotado | Ver seção "Por que o build é grande" -- usar `setup_mvp.ps1 -CpuOnly` antes de buildar |
| `package_release.py` falha com "arquivo proibido encontrado" | Sobrou algo da lista `FORBIDDEN_PATTERNS` (ex.: vídeo de teste copiado por engano) | Corrigir a origem do arquivo (não deveria estar no dist do Nuitka) e rodar de novo com `--force` |
| `package_release.py` falha com "peso de modelo vazou" | Algum `.pt`/`.onnx`/`.xml`/`.bin` apareceu no dist do Nuitka (não deveria — modelos são externos) | Confirmar que nenhum `--include-data-dir` do build aponta para `src/models`; se for falso positivo de outra dependência, adicionar o caminho exato a `ALLOWED_MODEL_WEIGHT_OVERRIDES` em `scripts/package_release.py` (mesmo padrão do caso `certifi/cacert.pem`) |
| Antivírus quarentena o `.exe` recém-buildado | Falso positivo comum em binários Nuitka não assinados | Whitelisting local para teste; para distribuição real, considerar assinatura de código (fora do escopo desta rodada) |
| "Definir como ativo" falha com "não é possível ativar" | O arquivo do modelo foi movido/apagado depois de importado, ou o par `.xml`/`.bin` ficou incompleto | Reimportar o modelo pelo Gerenciador de Modelos (Ctrl+Shift+M) |

## O que este build NÃO faz (limitações conhecidas desta rodada)

- Não gera instalador MSI/Inno Setup — só a pasta + ZIP.
- Não assina o executável digitalmente — reduz exposição do código-fonte
  (nenhum `.py` sobra na pasta final), mas não é proteção contra engenharia
  reversa avançada nem substitui assinatura de código para produção.
- Não compila o serviço headless (`tools/run_pediatria_service_api.py`) nem
  o `plugin/manifest.json` do contrato de plugin — só o Demo Viewer.
- Não testa em VM limpa automaticamente (ver "Próximo passo recomendado" no
  relatório de cada build).
