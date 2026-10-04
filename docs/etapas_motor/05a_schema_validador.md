# Lote 05a — Schema, validador e molde (antecipação da etapa 05)

Estado: **concluído em 04/10/2026**; commit: ver `git log` da branch (mensagem "schema e validador estatico de especialistas").
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md) · Branch: `feature/motor-plugins-nos`.
Relação: antecipa artefatos previstos na etapa 05. **A etapa 05 NÃO está concluída**: faltam instalação
pelo painel, ativação, retorno de versão, receitas e o segundo pacote de conformidade.

## Escopo entregue

1. JSON Schema versionado do manifesto (protocolo 1).
2. Validador estático de pasta e ZIP, com CLI e saída texto/JSON.
3. Molde declarativo para desenvolvimento externo.
4. Testes de conformidade e documentação de uso.

Fora deste lote (não feito): migração da pediatria, inferência, executor compartilhado, instalação pelo
painel, receitas, transporte remoto, fusão térmica, workspace externo.

## Decisões consolidadas (normativas em `manual_especialista.md`, seção 12)

* Hash de pasta OpenVINO: `jutta-dir-v1` (registros com comprimentos delimitados, ordenados por bytes UTF-8
  do caminho NFC). Arquivo único: SHA-256 dos bytes.
* Protocolo: igualdade exata com um protocolo suportado (hoje `{1}`); menor não é presumido compatível.
* Combinações tipo × mapeamento × agregação: tabela fechada (12.3); o resto é `combinacao_invalida`.
* Parâmetros ligam-se a `de.limiar_presente`/`limiar_ausente` por `{"$parametro": "nome"}`.
* Estático (feito) × exige carregar o modelo (não feito); a saída sempre lista o que não foi verificado.
* Limites e regras de nome do Windows como política inicial ajustável (12.6).
* Códigos de saída 0–6, com precedência 6 > 3 > 5 > 2 > 4 para vários erros.
* Pose estruturada, atributos derivados e térmico/fusão continuam **fora** do subconjunto (12.7).
* Só pacotes da equipe e sem código executável na v1.

Conferência pedida: o `motor_de_analise.md` já registrava pacotes declarativos sem Python de terceiros
(seção 8.1); não houve divergência a corrigir nesse ponto.

## Arquivos

| Novo | Função |
|---|---|
| `src/jutta_ped/especialistas/esquemas/manifest.v1.schema.json` | Schema estrito (independente de `plugin/schemas/`) |
| `src/jutta_ped/especialistas/fontes.py` | Pasta/ZIP seguros: limites, nomes, links, colisões |
| `src/jutta_ped/especialistas/hashing.py` | SHA-256 e `jutta-dir-v1` |
| `src/jutta_ped/especialistas/validador.py` | Validação semântica, arquivos, hashes, relatório |
| `src/jutta_ped/especialistas/cli.py`, `__main__.py` | CLI `validar` e `hash` |
| `especialistas_molde/` | Molde (sem pesos; reprovado até ser preenchido) |
| `tests/test_especialistas_validador.py` | 97 testes |

Alterados: `requirements-mvp.txt` (`jsonschema`), `docs/manual_especialista.md` (v0.2, seções 7 e 12),
`docs/protocolo_nos.md` (seção 14), `docs/plano_motor_plugins.md`, `docs/etapas_motor/01_contratos.md`.

## Como validar um pacote

```
python -m src.jutta_ped.especialistas validar <pasta-ou-zip> [--formato texto|json] [--host-protocolo 1]
python -m src.jutta_ped.especialistas hash <arquivo-ou-pasta>
```

## Validação executada (04/10/2026, Windows, Python 3.12.7)

* `tests/test_especialistas_validador.py`: **97 passaram**. Cobrem pasta e ZIP válidos, OpenVINO com
  `metadata.yaml`, exemplos inválidos do manual, hash incorreto, arquivo ausente, protocolo incompatível,
  fuga de pasta, nomes do Windows (arquivos e pastas), link simbólico em ZIP e junção em pasta, colisões
  (inclusive arquivo × pasta), limites, ZIP inválido, hash independente da ordem, JSON/códigos da CLI,
  precedência, determinismo e o exemplo do manual contra o schema.
* Suíte completa: **438 passaram, 6 puladas** (testes de navegador Edge/Qt, puladas como antes, sem
  relação com este lote).
* Os "pesos" dos testes são bytes sintéticos com hashes reais; **não são modelos executáveis**.

### Correção após revisão externa (mesmo dia)

Quatro achados reproduzidos e corrigidos, cada um com teste de regressão:

1. Entradas de **pasta** de ZIP eram ignoradas: `../escape/` passava com código 0 e `LEIAME.md` +
   `LEIAME.md/sub.txt` não eram vistos como conflito. Agora nomes de pasta são validados e há
   `colisao_arquivo_pasta`.
2. `1e400` num número do manifesto gerava `OverflowError` sem relatório. Agora números não finitos ou
   gigantes são recusados no parse (código 2) e há captura final `falha_interna_validador`.
4. Uma entrada `atalho/` marcada nos metadados como link simbólico passava como pasta comum (código 0):
   o tipo/link agora é verificado **antes** de separar arquivo de pasta; tipos especiais (FIFO, dispositivo,
   soquete) e metadados incoerentes com o nome também são recusados.
3. Valores **padrão** de parâmetros que invertem os limiares passavam só com aviso. Agora são recusados
   (`limiares_invertidos`); permanece aviso apenas quando as faixas permitem inverter em ajustes futuros.

## Limitações

* Conformidade estática ≠ modelo aprovado. Não foram validados: carregamento de pesos, classes de `.pt` e
  `.onnx`, custo, precisão, reprodução de amostras, autoria.
* Ainda não há instalador, executor nem uso do validador pelo painel.
* O instalador deverá reaplicar os mesmos limites e extrair em área isolada (o validador não extrai).
* Assinatura de pacote e terceiros continuam abertos (manual, seção 11).

## Próximo lote recomendado

Etapa 02 (bateria de referência), que não depende deste lote, ou, dentro da etapa 05, o ciclo de instalação
(upload → validar → instalar isolado → ativar → retornar versão) reutilizando o validador. Antes dos nós,
fechar as ambiguidades A1–A4 do protocolo.
