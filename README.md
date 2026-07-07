# WebGuardiao Pediatria

MVP pediatrico standalone extraido do WEB-11. Nao usa dispatcher, banco,
ChromeBridge, WebHT, WebAcesso ou configuracoes operacionais do WebGuardiao.

## Instalar

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mvp.ps1
```

Para instalar PyTorch somente CPU:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mvp.ps1 -CpuOnly
```

## Executar

- Interface: `iniciar_mvp.bat`
- Video sem interface: `rodar_video_headless.bat "C:\caminho\video.avi"`
- Testes: `testar_mvp.bat`

A interface aceita arquivo, URL RTSP/HTTP ou camera USB. Evidencias, crops,
prints e relatorios ficam em `pediatria_results/`.

O pacote usa por padrao:

```text
src\models\pediatria_child_detector_v6_jutta_openvino_model
```

Se a pasta OpenVINO nao existir, cai para `pediatria_child_detector_v6_jutta.pt`
e depois para `pediatria_child_detector_v5.pt`.

Variaveis uteis antes de rodar `iniciar_mvp.bat`:

```bat
set PEDIATRIA_DEVICE=cpu
set PEDIATRIA_CONF=0.25
set PEDIATRIA_MODEL=src\models\pediatria_child_detector_v6_jutta_openvino_model
set PEDIATRIA_REPORT=pediatria_results\v6_jutta_openvino_local\report.json
```

Para testar GPU com modelo `.pt`, use:

```bat
set PEDIATRIA_DEVICE=0
set PEDIATRIA_MODEL=src\models\pediatria_child_detector_v6_jutta.pt
iniciar_mvp.bat
```

O cooldown default continua em 120 segundos por camera e pode ser alterado
apenas para ensaios com `--cooldown-seconds`.

## Conteudo

- Modelos `pediatria_child_detector_v6_jutta` em PyTorch e OpenVINO.
- Modelo `pediatria_child_detector_v5.pt` como fallback.
- Detector, contexto de escala, companhia, popup e voz locais.
- Runners GUI e headless.
- Testes focados, revisor manual de dataset e relatorio dos videos reais.
- Manifesto SHA-256 em `.jutta-ped-package.json`.

Os videos reais nao sao exportados por padrao. Use `-IncludeValidationVideos`
no exportador somente quando o armazenamento no destino estiver autorizado.