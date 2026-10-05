# Revisão de 03b e encaminhamento para 05f

Data: 05/10/2026. Revisão do working tree, sem commit e sem alterar a implementação.

## Parecer

O desenho segue a direção acordada: Cena e mestre na mãe, especialistas retornando
atributos, relações e persistência nas receitas do host. A integração segue em sombra.
O pacote de papel é uma adaptação parcial; não representa a migração da pediatria.

A próxima entrega é **05f — configuração simples por câmera**, já especificada no
[plano](plano_motor_plugins.md#05f--configuração-simples-por-câmera-e-validação-em-dois-níveis).
Antes, corrigir a divergência temporal abaixo. Não abrir outro plano nem substituir
05f por otimização, nós remotos ou reprodução de todas as regras pediátricas.

## Achado: tempo indeterminado é creditado retrospectivamente

Em `src/jutta_ped/motor/regras.py`, `AvaliadorRegras._passo`, os caminhos de
indeterminado/ausência preservam `run_last` e `neg_last`. Quando a evidência retorna
dentro de `lacuna_max_s`, o intervalo inteiro é somado, incluindo a interrupção explícita.
Isso diverge da promessa de que indeterminado não completa tempo e não conta como negativo.

Reprodução executada com os auxiliares existentes de `tests/test_motor_regras.py`,
`duracao_s=2`, `encerrar_negativo_s=2`, `lacuna_max_s=2`:

| Sequência (segundos: estado) | Resultado atual |
|---|---|
| 0: sim; 1: sim; 1,5: indeterminado; 2: sim | `positivo_s=2`, transição `iniciado` |
| Após ativação: 3: não; 4: não; 4,5: indeterminado; 5: não | `encerrado`, motivo `resolvido_evidencia_negativa` |

O teste `test_indeterminado_curto_entre_sims_nao_quebra_a_corrida_mas_nao_soma_sozinho`
hoje exige justamente a soma do intervalo inteiro. Portanto a suíte verde não elimina
a divergência: é preciso ajustar também essa expectativa, não só acrescentar testes.

Para respeitar o contrato: preservar o tempo já confirmado durante uma pausa curta,
mas não creditar a ponte sobre uma amostra explicitamente indeterminada/ausente ao
retomar. A primeira evidência válida da retomada estabelece uma nova referência de
tempo; as seguintes podem voltar a acumular. Distinguir essa situação de quadros
não amostrados, cujo intervalo continua sujeito a `lacuna_max_s`. Manter os limites
de perda, os motivos de encerramento e o descarte por geração/configuração.
Cobrir tanto tempo positivo quanto negativo, sujeito ausente e retomada, sem sleeps.

## Verificação desta revisão

- 257 testes passaram: Cena, mestre, integração com runtime, regras, motor de regras,
  painel de contrato 2, corridas de sombra, lote Ultralytics e receita pediátrica de exemplo.
- As duas reproduções acima foram executadas separadamente da suíte e confirmaram o comportamento.
- Reexecutei também `tests/test_pacote_papel_paridade.py` com modelos reais OpenVINO:
  1 teste passou, 6 recortes disponíveis nesta execução, diferença máxima 0,0000.
  O resultado confirma paridade nos mesmos recortes, não qualidade nem o caminho
  completo de captura/crop. Não reproduziu a contagem de 12 recortes do relato anterior.
- Não foi repetida a suíte completa, a bateria de capacidade nem a avaliação oficial de qualidade.
- Vídeo/overlay via go2rtc e popups com vídeo real continuam sem aprovação nesta revisão.

## Prompt para continuar no Claude

Leia este arquivo e a seção 05f de `docs/plano_motor_plugins.md`.

1. Reproduza e corrija primeiro o achado temporal acima. Atualize o contrato e a
   expectativa existente que credita a pausa; demonstre regressões positivas e negativas.
2. Depois implemente 05f na sequência já aprovada. Abra o registro
   `docs/etapas_motor/05f_configuracao_camera.md` ao iniciar. Primeiro contratos
   mestre/crop e configuração persistida; depois painel e validação em dois níveis.
3. Mantenha uma receita e um especialista por câmera no modo simples, com vários
   atributos permitidos; câmeras diferentes podem escolher especialistas diferentes.
   Preserve as receitas anteriores e aplique as restrições no servidor.
4. Entregue diagnóstico só do mestre/crops sem exigir o classificador pediátrico.
   Separe ROI de margem horizontal/vertical/mínimo em pixels; preserve coordenadas,
   identidade, orçamento e configuração efetiva. Use o fluxo normal de cadastro.
5. Verifique a disponibilidade atual de go2rtc e das câmeras cadastradas: a ausência
   relatada antes não comprova ausência agora. Valide primeiro mãe/mestre/crops, depois
   especialista e receita, sempre em sombra. Registre impedimentos concretos sem
   declarar que testes com backend falso comprovam inferência ou vídeo reais.
6. Preserve alterações existentes e pesos locais. Não commite neste lote. Registre
   arquivos alterados, testes efetivamente executados, evidências reais e pendências.
   Não encerre 02, 03 ou 05 inteira por concluir um sublote.

Não é necessário repetir o histórico no retorno: informe a correção, o resultado de
05f e o que ainda impede seus critérios de aceite. Qualidade clínica, confirmação
humana dos vídeos e migração operacional continuam assuntos separados.
