# Cómo buscar mejor

Auction Watch funciona mejor si empezás con una búsqueda amplia, ejecutás una
corrida y ajustás los criterios de a poco. La aplicación nunca modifica tus
criterios automáticamente.

## Los campos principales

### Cualquiera de estos términos

Amplía resultados: alcanza con que aparezca uno. Para una mesa de pool, por
ejemplo: `mesa de pool, billar, pool`.

### Debe incluir todos

Restringe mucho: cada resultado debe contener todos los términos. Un ejemplo
útil es `mesa, ping pong`. Evitá mezclar conceptos sin relación: `autor, tapa
dura` no ayuda a encontrar consolas.

Si escribís tres o más términos separados por comas y dejás “Cualquiera” vacío,
la interfaz te sugerirá mover algunos. Es sólo una advertencia: podés guardar
igual y Auction Watch nunca cambia los criterios por su cuenta.

### Frases exactas y exclusiones

Usá frases exactas para nombres o modelos concretos, como `game boy color` o el
nombre completo de un álbum. Las exclusiones evitan falsos positivos: `réplica,
funda, lámina, roto`.

### Fuentes

- **Bavastro:** remates y lotes de su catálogo público.
- **Castells:** remates descubiertos en su sitio y lotes de su API pública.
- **Prado:** productos públicos identificados como remates.
- **Remotes:** remates y lotes visibles en su feed público.
- **TodoRemates:** categorías y publicaciones de su catálogo público.

Conviene comenzar con varias fuentes activas porque cada rematador publica
inventario distinto. Una fuente parcial no oculta los resultados sanos de las
demás.

### Categorías, precio, urgencia y frecuencia

Las categorías y el precio máximo reducen ruido. El editor actual aplica un
máximo; todavía no ofrece un mínimo editable, por lo que esa limitación debe
considerarse al revisar resultados. Para una búsqueda urgente usá más horarios
y notificaciones. Para una búsqueda tranquila, menos frecuencia.

La automatización de cada perfil se controla con un interruptor independiente.
Al activarla, indicá uno o más horarios `HH:MM` y una zona IANA, por ejemplo
`America/Montevideo`. La cabecera muestra si el worker y el scheduler global del
entorno están realmente activos. Una corrida manual `completed` o `partial` con
snapshot, hecha hasta 15 minutos antes del horario o durante su ventana de
ejecución, cubre ese slot y evita repetirla. El scheduler sólo recupera un
horario durante los 15 minutos siguientes: un reinicio varias horas más tarde
no dispara una corrida inesperada.

## Qué significa la cobertura

- **complete:** la fuente verificó toda la cobertura prevista. Cero hallazgos
  puede interpretarse dentro de los criterios elegidos.
- **partial:** hay resultados válidos, pero falta una parte verificable. Conservá
  el snapshot y revisá la causa antes de cerrar más la búsqueda.
- **failed:** la fuente no entregó cobertura publicable. No interpretes cero
  como ausencia y mantené otras fuentes activas.

“Sin hallazgos” no demuestra que no existan remates: los criterios pueden ser
demasiado cerrados, quizá no haya inventario relevante o una fuente puede haber
quedado parcial.

## Recetas iniciales

- **Consolas:** cualquiera `consola, playstation, nintendo, sega, xbox`; frases
  `game boy, family game`; excluir `funda, lámina, libro`.
- **Discos:** cualquiera `vinilo, disco, LP`; frase con artista o álbum; excluir
  `decorativo, reloj`.
- **Libros:** cualquiera `libro, novela, colección`; frase con autor o título;
  excluir `revista, fotocopia`.
- **Mesa de pool:** cualquiera `mesa de pool, billar, pool`; excluir `miniatura,
  juguete`.
- **Mesa de ping pong:** cualquiera `ping pong, tenis de mesa`, o todos `mesa,
  ping pong`; excluir `paleta, pelota, red`.

## Flujo recomendado

1. Crear un perfil.
2. Elegir varias fuentes.
3. Comenzar con pocos términos en “Cualquiera”.
4. Ejecutar una corrida.
5. Ajustar exclusiones, categorías y precio.
6. Activar frecuencia y notificaciones cuando el resultado sea útil.
