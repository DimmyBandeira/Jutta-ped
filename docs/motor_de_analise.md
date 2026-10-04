# Motor de análise — desenho e plano

Documento vivo. Registra o que foi decidido na discussão e o estado da implementação. Idioma e estilo do
projeto: português, sem jargão desnecessário.

## 1. Objetivo

Sair de um detector pediátrico fixo para um **motor genérico**: um detector mestre (pessoa) alimenta
extratores de atributos sobre os recortes, e **regras** (do sistema ou cadastradas) transformam atributos em
alertas. A pediatria ("criança desacompanhada") passa a ser um conjunto de regras do sistema, não código
especial.

## 2. Pipeline em cascata

```
frame ─► mestre (pessoa, ~5 fps, CPU) ─► rastreamento ─► extratores por recorte ─► regras ─► alerta
                                          │                 ├ geometria (altura, proporções)
                                          │                 ├ classificador criança/adulto (v5/v6)
                                          │                 ├ pose → orientação, mão perto da cabeça…
                                          │                 ├ bebê no colo / carrinho (Bebe-no-colo.pt)
                                          │                 └ calor (só cores, fonte térmica)
                                          └─► caixa "analisando" (única caixa desenhada)
```

* Só o **mestre** desenha caixa, rotulada "analisando". A cor vai do neutro ao vermelho conforme a
  pontuação se aproxima do limiar das regras cadastradas; vermelho = alerta gerado.
* Atributos de pose são **tri-estado** (sim / não / indeterminado) com altura mínima da pessoa na imagem:
  abaixo disso o atributo é "indeterminado", nunca "não".
* Duas inferências no mesmo frame são esperadas (mestre em todo ciclo; especialistas só nos recortes, com
  cache por track e orçamento por ciclo).

## 3. Regras e pontuação por peso

Cada regra tem condições **obrigatórias** e **evidências ponderadas**. Dispara quando as obrigatórias
valem e a soma dos pesos ≥ limiar. A porcentagem da caixa "analisando" é soma ÷ limiar; faixas de pontuação
definem a criticidade (média / alta / crítica). Exemplo:

| Condição | Peso |
|---|---|
| Criança sem adulto próximo (obrigatória) | 60 |
| Calor acima do ambiente | 25 |
| Adulto de costas para a criança | 10 |
| Persistência acima de 5 s | 5 |

## 4. Receitas

Receita = conjunto nomeado de extratores + regras + parâmetros, **habilitada por câmera** na aba de
câmeras (com ajustes numéricos por câmera). A receita padrão "Pediatria" é do sistema: somente leitura,
clonável. Todo alerta registra `receita_id` e `receita_versao`. Aplicar mudança reinicia só as câmeras
afetadas.

## 5. Câmera térmica (bi-espectral)

Câmera com duas fontes na mesma câmera cadastrada (campos `termica_*`). A receita declara "Visível" ou
"Visível + térmico". Observado nos pares reais: campos de visão, resolução, fps e paleta diferem entre os
dois streams → exige **calibração por câmera** (homografia a partir de pares de pontos), sincronismo por
último frame com tolerância (~300 ms) e leitura relativa (índice de calor, não graus). Um componente de
**fusão** junta as observações por pessoa antes das regras. Sem calibração, o extrator térmico fica
desligado. Triagem de febre fica fora do escopo (precisa de stream radiométrico).

## 6. Colocação da inferência (dispositivo)

Política em `src/jutta_ped/service/placement.py`:

* modelo OpenVINO → CPU (ou `intel:gpu` se pedido);
* modelo `.pt` → `cuda:N` se houver CUDA, senão CPU;
* pedido explícito de CUDA troca os OpenVINO pelo `.pt` irmão; sem CUDA ou sem `.pt`, cai para CPU com o
  motivo registrado, sem falhar a sessão.

O dispositivo declarado agora é o que de fato executa (antes `auto` dizia `cuda:0` com modelos rodando na
CPU, e o perfil econômico de CPU não era aplicado). Meta futura: mestre na CPU, recortes/bebê na GPU com
transbordo dinâmico.

## 7. Cadência e tempos em segundos

* `SessionStartConfig.analysis_fps` (padrão 5; piso operacional 3) limita o laço de fontes **ao vivo**;
  lê sempre o frame mais recente. Arquivos nunca são limitados.
* Tempos do analisador de companhia em segundos (`CompanionshipTiming`), convertidos pela taxa efetiva
  (`CompanionshipConfig.from_seconds`). Referência dos defaults antigos: 15 fps (1 s, 1 s, 1 s, 3 s).
* `set_analysis_fps()` permite ao governador de carga ajustar a quente.

## 8. Banco e migrações

Alembic em `src/jutta_ped/painel/migracoes/`. `Database.migrar()` (chamado por `create_all`) aplica as
revisões; banco legado sem `alembic_version` recebe o carimbo da revisão 0001 e segue.

* 0001 — esquema base (o que existia).
* 0002 — alertas genéricos (`plugin`, `tipo`, `severidade`, `status`, `receita_id`, `receita_versao`) e
  segunda fonte da câmera (`termica_canal`, `termica_rtsp_url_enc`, `termica_stream_name`).

`status` do alerta: novo → reconhecido → resolvido | descartado.

## 9. Estado e ordem de execução

| Etapa | Estado |
|---|---|
| Fundamentos: dispositivo honesto + CUDA com reserva em CPU | feito |
| Fundamentos: tempos em segundos + `analysis_fps` | feito |
| Fundamentos: migrações + esquema genérico + campos da fonte térmica | feito |
| Motor genérico: detecções unificadas, regras do sistema, extratores, caixa "analisando" | a fazer |
| Receitas (CRUD, habilitar por câmera, ajustes, aplicação a quente) | a fazer |
| Telas: leitor de modelos, catálogo de extratores, regras, receitas, mosaico de 8 câmeras com tela cheia, Central de Alertas e popups em janelas, página Servidor + governador de carga | a fazer |
| Fusão térmica (calibração, sincronismo, extrator de calor) | a fazer |
| Fase 5: relatório, destinos (GPS Indoor/WifiPTT/Webhook), retenção de evidências | adiada |

## 10. Pendências conhecidas (não resolvidas)

* v6 rotula adulto como criança em alguns casos; mestre a 320 px deixa pessoas sem caixa.
* Popups repetidos por troca de ID de track (a trava usa câmera+estado+track); agrupar por câmera não foi
  aprovado ainda.
* `Bebe-no-colo.pt` (YOLO11s, 960 px; classes bebe_colo, bebe_conforto, carrinho, bolsa_termica,
  carrinho_perinatal) ainda sem decisão de versionamento.
