# Próximo lote — Cena genérica e regras espaciais/temporais na mãe

Revisão em 2026-10-05 do working tree após `d16dab6`. Dashboard alinhado ao mockup; correções de snapshot/configuração, aposentadoria por montagem e semântica de streams implementadas. A validação de vídeo/overlay reais e popups móveis continua pendente: não impede este desenvolvimento em sombra, mas impede declarar a validação ao vivo concluída.

Execute na branch `feature/motor-plugins-nos`, preservando as alterações anteriores. Não fazer commit automático, push, versionamento de pesos ou imagens de pessoas. O foco continua no sistema-mãe genérico; especialista produz atributos e a mãe decide relações, persistência e eventos.

## 0. Fechar a lacuna de geração antes de ampliar o contexto

Reprodução do Codex: bloquear uma inferência da geração 1 já em execução; enviar a geração 2 pela mesma PortaSombra; liberar a primeira. Rastro resultante: `(geracao=1, aposentado=false)` e `(geracao=2, aposentado=false)`; ambos entram no feed. A identidade da montagem não muda quando a sessão muda. O despacho descarta pendentes antigos, mas não invalida a publicação do que já estava executando.

O teste `test_reinicio_de_sessao_com_trabalho_em_andamento_descarta_a_geracao_obsoleta` tem uma asserção que substitui geração ausente por 2 e aceita alternativa por quadro. Isso mascara a falha: o formatador não preserva a geração.

Propagar geração no evento de diagnóstico e invalidar a publicação antiga assim que a nova geração for aceita, sem bloquear o laço. Resultado antigo pode ir ao histórico explicitamente classificado, nunca ao feed atual. Criar regressão com barreiras, geração explícita e sem fallback que transforme ausência de dado em sucesso. Preservar correções de desligamento e troca de receita. Não redesenhar o dashboard neste lote.

## 1. Extrair mestre e Cena

Ler `docs/transicao_motor_de_analise.md`, `docs/manual_especialista.md`, registro 03 e pontos pertinentes do runtime e pipeline pediátrico. Criar registro `docs/etapas_motor/03b_cena_regras.md`, vinculado ao plano e à etapa 03.

Definir Cena genérica: câmera, geração, quadro, timestamp local de recepção, dimensões, conjunto de trilhas/detecções com classe mestre, bbox, confiança e identidade. Estabelecer unidades, limites de validade e propriedade/cópia dos dados para execução assíncrona. Cena sem detecções precisa avançar expiração e estado temporal; ausência de trilha não equivale a resultado negativo do especialista.

Separar detecção/rastreamento do processamento pediátrico completo, reutilizando os modelos e o código existentes. Demonstrar uma captura e uma passagem pelo mestre por quadro compartilhado; não carregar um segundo mestre para fabricar independência. A integração genérica deve funcionar sem executar primeiro o classificador/regras de pediatria. Preservar o caminho operacional anterior durante a migração em sombra.

## 2. Regras entre trilhas e persistência

Implementar na mãe primitivas mínimas e testáveis para relacionamento entre sujeitos, proximidade espacial e persistência temporal. Parâmetros e papéis vêm da receita/atributos; o núcleo não escolhe comportamento pelo nome do pacote nem pelo nome da receita. Documentar a métrica geométrica: distância em pixels/normalizada não prova distância física em metros.

Tempo deve vir de timestamps, não de quantidade de frames. Isolar estado por câmera, geração, receita/configuração e sujeitos. Reconfiguração, término de sessão, perda/reaparecimento de trilha, resultado atrasado, expiração e indeterminado precisam ter comportamento explícito. Não afirmar identidade entre IDs de rastreamento diferentes sem um mecanismo comprovado.

Política inicial para evento ativo em sombra: indeterminado não o encerra como resolvido e não conta como evidência positiva para iniciar um novo. Registrar perda de sustentação e idade da última evidência. Persistência positiva exige evidências válidas; intervalos indeterminados não podem completar silenciosamente o tempo exigido. Definir encerramento por evidência negativa sustentada ou por política explícita de perda de observação, com motivo distinto de resolução. Tudo permanece em sombra, sem encaminhar ao dispatcher operacional.

Evoluir/versionar o contrato de receitas preservando as receitas simples já persistidas e a interface que as usa. Atualizar validação e persistência conforme necessário; não duplicar regras em rotas. Não é necessário construir um editor visual completo das novas relações neste lote: documentar o suporte da interface e recusar configuração que ela não consiga preservar ao editar, sem apagar campos silenciosamente.

## 3. Especialista de papel e prova de generalidade

Inspecionar o classificador criança/adulto atual e demonstrar se entrada, pré-processamento, classes e saída cabem no pacote declarativo. Documentar eventual incompatibilidade antes de qualquer treino. Não substituir esse classificador por bebê no colo nem transformar contexto/companhia em responsabilidade do pacote.

Se compatível, preparar adaptação e receita pediátrica em sombra. Se não, entregar a fronteira genérica funcionando e a incompatibilidade precisa, sem simular uma migração concluída. Exercitar também uma classe mestre diferente de `person`, com pacote e receita de conformidade. Dados sintéticos provam contrato e generalidade; não provam qualidade do modelo real.

## 4. Evidências de conclusão do lote

- Cena obtida sem passagem prévia pela lógica pediátrica; contagem de chamadas/carga comprova reutilização do mestre.
- Duas câmeras e múltiplas trilhas; timestamps irregulares, cena vazia, desaparecimento, expiração, troca de receita e reinício de sessão.
- Relações e persistência reproduzíveis com relógio controlado; indeterminado não produz resolução falsa nem satisfaz persistência positiva.
- Geração antiga em execução não publica no feed vigente; desligar/reconfigurar continuam funcionando.
- Receita simples antiga mantém comportamento; nenhuma alteração silenciosa de dados pelo formulário atual.
- Pacote não pediátrico passa pelo mesmo fluxo. Fumaça real somente quando disponível e pertinente, com evidência separada da qualidade.
- Testes focados, depois suíte pertinente; não repetir bateria de capacidade. Comparação oficial da migração depende da etapa 02, que continua aberta.

Pode usar até dois subagentes com responsabilidades separadas: um para mestre/Cena/adaptador, outro para regras/estado temporal/testes, após o coordenador fixar o contrato comum. Coordenador integra runtime e painel e revisa. Não usar `/batch` em mudanças dependentes. Não implementar nós remotos, térmico, treino ou executor compartilhado sob carga neste lote.

Entregar código, testes e documentação, não apenas plano. Relatório final curto: o que a mãe já faz independentemente da pediatria; resultado das regressões; estado da adaptação do classificador; o que permanece em sombra e pendente. Etapas 02, 03 e 05 só serão encerradas conforme seus critérios, sem arquivamento prematuro.
