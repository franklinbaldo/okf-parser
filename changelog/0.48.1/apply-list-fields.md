## `apply` agora escreve listas planas de frontmatter

Campos de lista plana passam a ser colunas graváveis no `okf-parser apply`, incluindo operações DuckDB como `list_append` e substituição por literais de lista. O writer preserva listas como YAML estruturado em vez de serializá-las como strings.

Listas declaradas em `.schema.sql` mantêm o tipo de lista e os constraints `NOT NULL` e `CHECK` aplicáveis. Mapas, listas aninhadas e campos que misturam valores escalares e listas continuam fora do namespace gravável.
