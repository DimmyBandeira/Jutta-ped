# Molde de especialista declarativo (protocolo 1)

Ponto de partida para criar um especialista no workspace separado. **Este molde NÃO é um pacote
executável e NÃO traz pesos**: ele é reprovado pelo validador até você preenchê-lo.

## O que preencher

1. Copie esta pasta e renomeie.
2. Coloque seus pesos em `modelos/` (arquivo `.pt`/`.onnx`, ou pasta OpenVINO com `.xml` + `.bin`; para
   OpenVINO inclua o `metadata.yaml` gerado pelo Ultralytics, que permite conferir as classes).
3. Em `manifest.json`: `pacote.id` (domínio reverso, único e estável), `versao`, `nome`, `autor`;
   `modelos[0].arquivo` e `formato`; `atributos[]` mapeando as **classes reais do seu modelo**;
   `custo` com a sua medição.
4. Calcule o hash dos pesos e cole em `modelos[0].sha256`:
   `python -m src.jutta_ped.especialistas hash modelos/MEU_MODELO.pt` (arquivo) ou
   `python -m src.jutta_ped.especialistas hash modelos/meu_modelo_openvino_model` (pasta).
5. Opcional: 5–20 recortes reais em `amostras/` (`001.jpg`) com `001.esperado.json`
   (`{"atributos":[{"nome":"meu_atributo","estado":"definido","valor":"sim"}]}`).
6. Valide: `python -m src.jutta_ped.especialistas validar <pasta-ou-zip>`.
7. Empacote em `.zip` com `manifest.json` na raiz (não dentro de uma subpasta).

## O que o validador garante (e o que não)

Conformidade **estática**: manifesto, referências, arquivos, hashes e limites. Não carrega os pesos e não os
executa; portanto não verifica funcionamento, classes de `.pt`, desempenho nem autoria. Veja
[docs/manual_especialista.md](../docs/manual_especialista.md).

## Regras que mais derrubam pacotes

* Sem código: nada de `.py`, `.dll`, `.sh` etc. Só `.json .md .txt .jpg .png .pt .xml .bin .onnx .yaml`.
* `cpu` obrigatório em `dispositivos`; pesos dentro de `modelos/`.
* `validade_ms` em todo atributo; indeterminado nunca é "não".
* Pesos `.pt` (pickle) só para pacotes da equipe; terceiros devem usar OpenVINO ou ONNX.
