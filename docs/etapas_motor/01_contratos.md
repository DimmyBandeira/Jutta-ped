# Etapa 01 — Contratos autossuficientes

Estado: **em andamento — rascunhos dos dois documentos entregues; etapa NÃO encerrada** (bloqueios de
interoperabilidade abaixo).
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md).
Branch: `feature/motor-plugins-nos`.

## Objetivo

Permitir que especialistas sejam desenvolvidos em outro workspace usando uma especificação
publicada pelo sistema-mãe, sem consultar suas classes ou estruturas internas. Definir também
como os nós executam esse mesmo contrato, mantendo regras e alertas na mãe.

## Arquivos desta etapa

Criar `docs/manual_especialista.md` e `docs/protocolo_nos.md`. São entregas planejadas: ainda não
foram produzidas nesta abertura. Preservar os três documentos de arquitetura durante a escrita
em paralelo por outro assistente; divergências devem ser apontadas para consolidação explícita.

### Manual do especialista

Documentar as quatro peças da entrega futura:

1. **Manifesto:** especificação do JSON Schema versionado, `protocolo: 1`, identidade do pacote,
   modelos e checksums, capacidades, parâmetros e política de compatibilidade. Apresentar campos
   obrigatórios, restrições e exemplos válidos/inválidos.
2. **Execução:** lote de recortes, formato e pré-processamento, contexto disponível, identidades,
   prazo, atributos tipados, indeterminação, validade por atributo/receita e erros. Esclarecer
   quais dados o especialista recebe e quais responsabilidades ficam no host.
3. **Molde declarativo:** árvore de pacote mínima e conteúdo ilustrativo suficientes para orientar
   o workspace separado. Pesos de exemplo devem ser identificados como referência, sem fingir que
   um pacote executável já foi entregue. Sem Python de terceiros instalado pelo pacote.
4. **Validador:** especificar a interface pretendida de um comando, entradas, saídas, códigos de
   resultado e verificações de manifesto, arquivos, pesos, checksums e compatibilidade. Identificar
   claramente que o comando executável será entregue em implementação posterior.

### Protocolo dos nós

Reutilizar o contrato de execução do manual por referência, sem criar outro formato de atributos.
Definir conexão, autenticação, capacidades e versões, admissão de carga, pedido lógico/tentativa,
orçamento restante, resultados atrasados/duplicados, falha e retomada, métricas e evolução.
Separar decisões fechadas de alternativas de transporte ainda em avaliação.

## Critérios de conclusão

- Os dois documentos existem, têm versão/estado e distinguem obrigação de proposta.
- Um desenvolvedor consegue montar conceitualmente o pacote e interpretar pedidos/resultados
  apenas com a documentação e seus exemplos, sem ler código do sistema-mãe.
- Manifesto, execução e protocolo usam identidades, validade e versões coerentes.
- Exemplos cobrem sucesso, indeterminado, pacote inválido/incompatível, prazo expirado, queda do nó,
  tentativa local concorrente e resposta antiga após reinício da sessão.
- Nenhum exemplo permite renovar prazo por retentativa, reutilizar cache expirado como certeza
  ou aceitar resultado antigo sobre atributo mais novo.
- Pontos ainda não resolvidos são listados com impacto. Uma questão que impeça implementar
  interoperabilidade bloqueia o fechamento do contrato correspondente.
- Links e exemplos documentais foram revisados. A conclusão documental não é apresentada como
  validação de um sistema executável.

## Fora desta entrega

Implementar schemas, runtime, validador ou instalador; executar benchmarks; criar workspace
externo; instalar modelos ou operar nós. A bateria de referência pode avançar como outra frente
quando iniciada, sem ser pré-requisito para escrever estes documentos.

## Situação em 04/10/2026

* Entregues como rascunho: [manual do especialista](../manual_especialista.md) (v0.2) e
  [protocolo dos nós](../protocolo_nos.md) (v0.1). O lote [05a](05a_schema_validador.md) consolidou o
  contrato implementável do especialista (manual, seção 12) e implementou schema, validador e molde.
* **Fechado:** hash canônico de pasta OpenVINO, política de versões, combinações de atributo, ligação de
  parâmetros, limites iniciais, escopo estático do validador.
* **Bloqueios de interoperabilidade que impedem encerrar a etapa:**
  1. Ambiguidades A1–A4 do protocolo dos nós (reenvio remoto, créditos, validade desde a captura,
     cancelamento) — [protocolo, seção 14](../protocolo_nos.md#14-ambiguidades-registradas-para-resolução-posterior).
  2. Pose estruturada, atributos derivados e térmico fora do `protocolo: 1` (manual, 12.7); o motor os prevê.
  3. Transporte dos nós ainda em avaliação (proposta: WebSocket).
* Critério "exemplos de nó coerentes" depende de fechar A1–A4; critério "autor externo monta o pacote sem
  ler o código da mãe" tem agora validador e molde, mas ainda não foi testado por um autor externo.

## Resultado de encerramento

Pendente. Ao concluir, preencher data, arquivos entregues, validações, decisões ainda transferidas
para outras etapas e commits disponíveis. Mover este registro para o histórico conforme o plano;
os dois contratos permanecem em `docs/` para uso e evolução.
