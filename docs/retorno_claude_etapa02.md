# Retorno ao Claude — fechar confiabilidade da avaliação

Base: branch `feature/motor-plugins-nos`, commit `59de470`; revisão em 2026-10-04. A etapa 02 está em andamento. O objetivo maior continua sendo sistema-mãe e especialistas; pediatria fornece a referência da migração.

## Lote autorizado pelo prompt que aponta para este documento

1. Leia [revisão do Codex](referencia/2026-10-04/revisao_codex.md), `tools/bateria/episodios.py`, `tests/test_bateria_referencia.py` e seção 6 de `docs/referencia/README.md`. Abra outros arquivos apenas conforme necessário.
2. Reproduza e corrija os quatro achados do avaliador: associação exclusiva, validação da anotação/proveniência, semântica de trilhas e cobertura integral/timeout. Acrescente regressões dos contraexemplos descritos. Trate sobreposição de sujeitos explicitamente; não invente associação por pessoa quando o registro não a contém.
3. Preserve os JSON de `propostas_ia/` como propostas. Não mude seus booleanos para fabricar confirmação humana. `stream-normal.mp4` é candidato a negativo, ainda sem análise e sem origem confirmada. Prepare o caminho para a confirmação, mantendo as pendências visíveis.
4. Atualize o procedimento e o registro `docs/etapas_motor/02_referencia.md`. Execute testes focados e depois a suíte pertinente. Relate arquivos, testes e pendências brevemente. Não encerre a etapa enquanto faltarem os critérios de qualidade.

Não refaça matriz/perfil/escala, não implemente migração ou nós neste lote e não versione pesos, vídeos ou quadros. Preserve `Bebe-no-colo.pt`. Sem push ou publicação. Não altere números históricos silenciosamente; se recalcular algo, registre a versão e a origem.

## Divisão sugerida

Use no máximo dois subagentes se estiverem disponíveis: um implementa avaliador e testes; outro revisa o contrato de anotação e as propostas, somente leitura, devolvendo achados curtos. O coordenador integra, atualiza documentos e valida. Evite duas pessoas/agentes editando o mesmo arquivo. Inferência e ensaios de desempenho devem rodar isoladamente, sem disputa de CPU/GPU. Não é necessário criar uma equipe persistente.

## Claude Code: escolha do modo e economia

Consulta ao manual oficial em 2026-10-04. O executável `claude` não foi encontrado no PATH desta sessão; a disponibilidade na instalação do usuário não foi verificada.

- O comando é `/batch`, não `/bath`. Ele decompõe mudanças grandes em 5–30 unidades independentes, com subagentes e worktrees. **Não recomendo para este lote**, que concentra mudanças no mesmo avaliador. Use delegação pontual. [Comandos oficiais](https://code.claude.com/docs/en/commands).
- Subagentes também consomem tokens. Podem poupar o contexto principal ao devolver resumos, mas paralelismo não garante menor custo. Aqui o ganho esperado é evitar releituras do histórico: iniciar conversa nova com este arquivo, restringir leituras e pedir saída curta. Consulte `/context` e `/usage`; para continuar uma conversa extensa, use `/compact` com as decisões a preservar. [Custos](https://code.claude.com/docs/en/costs), [subagentes](https://code.claude.com/docs/en/sub-agents).
- No terminal, confira `claude --version` e `claude --help`. Para execução não interativa, `--max-turns` e `--max-budget-usd` são opções de `-p`; não são um teto de tokens da conversa interativa. Não há orçamento financeiro definido para este lote, portanto não foi inventado um valor. [Referência da CLI](https://code.claude.com/docs/en/cli-reference).

## Prompt para colar numa conversa nova, aberta neste repositório

```text
Leia docs/retorno_claude_etapa02.md e execute somente o lote descrito nele.
Use até 2 subagentes com tarefas separadas, se disponíveis; não use /batch.
Preserve a distinção entre proposta visual por IA e revisão humana confirmada.
Não repita a bateria de capacidade. Entregue correções, testes e pendências
em resposta curta, sem recontar o histórico do projeto.
```
