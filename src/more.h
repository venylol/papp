/* more.h
 *
 *  - prototypes pour more.c
 *  - prototypes for more.c
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */


#ifndef __More_h__
#define __More_h__


#include "changes.h"


extern long  nbrOfLines, nbrOfColumns;

long yes_no(const char *prompt);

void more_init(const char *filename);   /* prepare l'ecran pour more - prepare screen for more     */
void more_line(const char *line);       /* affiche une ligne avec more - display a line with more  */
void more_close(void);                  /* termine avec more  - end with more                      */
void more_set_mode(const char *mode);   /* change le mode d'ouverture du prochain fichier avec more -
                                         *  change next file opening mode with more                 */
char *more_get_mode(void);              /* recupere le mode d'ouverture du prochain fichier avec more -
                                         *  retreive next file opening mode  with more              */

/* Gestion de la console - console management */

long read_key(void);
char *read_Line(void);
long read_Line_init(char *buffer, long len, long compl_flag);
void clearScreen(void), clear_line(void), screen_bottom(void);
void reverse_video(const char *);

void beep(void);
void goodbye(void);

/* Divers  - miscellaneous */

long getRandom(long n);
long put_lock(void);
void remove_lock(void);
void init_screen(void), screen_reset(void);
void init_keyboard(void), keyboard_reset(void);
long print_file(char *filename);
long isFIleExist(char *filename);

#if defined(PAPP_MAC_METROWERKS)
    void init_mac_SIOUX_console(void);
#endif

#endif  /* #ifndef __More_h__ */
