/*
 * more.c: affichage d'un texte avec pagination. Ce fichier contient,
 * dans sa deuxieme moitie, bon nombre de fonctions tres dependantes de
 * la machine (affichage, lecture du clavier, impression).
 *
 * (EL) 16/10/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 *****
 *
 * more.c: page display of a text. This file contains, in his second half,
 * a lot of machine-dependent functions (display, keyboard reading, printing)
 *
 * (EL) 16/10/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <string.h>
#include <ctype.h>
#include <time.h>



#include "changes.h"
#include "more.h"

#ifdef atarist
# include <osbind.h>
#elif defined(__MSDOS__)
# ifdef __GO32__
#  include <gppconio.h>
#  include <pc.h>
# else
#  include <conio.h>
#  include <bios.h>
# endif
# include <dos.h>
# include <io.h>
#elif defined(__THINK_C__)
# include <console.h>
#endif

#if defined(UNIX_BSD) || defined(UNIX_SYSV)
# include <unistd.h>
# include <termios.h>
# include <sys/signal.h>
# include <sys/ioctl.h>
# include <sys/file.h>
# include <sys/types.h>
#endif

#if defined(CYGWIN) || defined(MACOSX) || defined(LINUX)
#include <signal.h>
#endif

#if defined(TERMCAP) && !defined(NO_TERMCAP_H)
    #include <term.h>
    #include <termcap.h>
#endif


#ifdef UNIX_SYSV
# include <fcntl.h>
#endif

#include "global.h"

static long nbld, nble, do_print, is_pipe;
static FILE *more_fp;
static char more_opening_mode[10] = "w";

long nbrOfLines=0, nbrOfColumns=0, auto_wrap=-1;

/*
 * long yes_no(const char *prompt); affiche "prompt" a l'ecran et attend que l'utilisateur
 * reponde 'O' ou 'N'. La fonction renvoie 1 pour oui et 0 pour non.
 *
 * Evidemment, en anglais ce sera different (y/n).
 *
 *****
 *
 * long yes_no(const char *prompt); displays "prompt" on screen and wait for the user to answer
 * 'Y' or 'N'. Function returns 1 for Yes and 0 for No.
 *
 * Of course, it will be different in French (o/n)
 */

#ifdef ENGLISH
# define ANSWER_YES "Yes"
# define ANSWER_NO  "No"
#else
# define ANSWER_YES "Oui"
# define ANSWER_NO  "Non"
#endif

long yes_no (const char *prompt) {
    char c;

    assert(prompt && *prompt);
    printf("%s ", prompt);
    while(1) {
        c = read_key();
        if (tolower(c) == tolower(*ANSWER_YES)) {
            puts(ANSWER_YES);
            return 1;
        }
        if (tolower(c) == tolower(*ANSWER_NO)) {
            puts(ANSWER_NO);
            return 0;
        }
    }
}

/*
 * La fonction gets() ne fait decidement pas ce que je veux! En voici une autre,
 * tres semblable a celle utilisee pour l'entree des joueurs. On peut sortir par ^J,^M ou ^D.
 * Les touches ^[ et ^X vident le buffer, ^H et Del detruisent le dernier caractere;
 * ^W detruit le dernier mot, ^T permute les deux derniers caracteres, tandis que ^C vide le buffer
 * et quitte. La chaine fera au plus 127 caracteres (+un caractere nul) et doit
 * si necessaire etre copiee dans un endroit sur.
 *
 *****
 *
 * Function gets() doesn't do what I want. Here is another one, very close to the one used
 * to enter the players. ^J, ^M and ^D exit, ^[ and ^X empty the buffer, ^H and Del destroy
 * the last character, ^W destroys the last word, ^T swaps the two last characters, and ^C
 * empties the buffer and quits. String will be at most 127 character long (+ empty byte) and
 * must, if necessary, be copied in a safe place.
 */

#define  CCTL(x)    (x-'@')
#define  BACKSPACE  '\b'
#define  CARR_RETURN    '\r'
#define  LINE_FEED  '\n'
#define  TABULATION '\t'
#define  ESCAPE     CCTL('[')
#define  DELETE     127

char *read_Line (void) {
#define  MAX_LENGTH   127
    static char buffer[MAX_LENGTH+1];

    *buffer = '\0';
    if (read_Line_init(buffer, MAX_LENGTH, 0) < 0)
        *buffer = '\0';
    return buffer;
}

long read_Line_init (char *buffer, long maxlen, long complete) {
    char *p, c;

    p = buffer + strlen(buffer);
    printf("%s", buffer);
    while (1) {
#ifdef CURSOR

    #ifdef PAPP_MAC_METROWERKS
        inv_video(" ");
        c = lire_touche();
        /* 2 backspaces car inv_video a rajoute un espace - 2 backspaces because reverse_video added a space */
        printf("\b\b");
    #else
        printf("~\b");
        c = read_key();
        printf(" \b");
    #endif

#else
        c = read_key();
#endif
        if (c == CARR_RETURN || c == LINE_FEED || c == CCTL('C')) {
            /* Fin de la ligne - end of line */
            return (c == CCTL('C') ? -1 : 0);
        }
        else if (complete && (c == CCTL('D') || c == TABULATION)) {
            /* Demande de completion - asking for completion */
            return 1;
        }
        else if (c == BACKSPACE || c == DELETE) {
            /* Effacer le dernier caractere du tampon - removes last character in buffer */
            if (p == buffer)
                beep();
            else
                *--p=0,printf("\b \b");
        }
        else if (c == CCTL('U') || c == CCTL('X') || c == ESCAPE) {
            /* Effacer tout le tampon - clears all buffer */
            while (p > buffer)
                --p,printf("\b \b");
            *p = 0;
        }
        else if (c == CCTL('T')) {
            /* Transposer les deux derniers caracteres - swaps two last characters */
            if (p-buffer < 2)
                beep();
            else {
                char d;
                d = p[-2]; p[-2] = p[-1]; p[-1] = d;
                printf("\b\b%c%c", p[-2], p[-1]);
            }
        }
        else if (c == CCTL('W')) {
            /* Effacer le dernier mot - clears last word */
            if (p == buffer)
                beep();
            while (p > buffer && p[-1] == ' ')
                --p,printf("\b \b");
            while (p > buffer && p[-1] != ' ')
                --p,printf("\b \b");
            *p = 0;
        }
        else if ((unsigned)c < 32) {
            /* Caractere de controle non reconnu - unknown control character */
            beep();
        }
#ifndef EIGHT_BITS
        else if ((unsigned)c > 127) {
            /* les caracteres >= 128 sont interdits - chracters >= 128 are forbidden */
            beep();
        }
#endif
        else if (p-buffer >= maxlen) {
            /* Tampon plein - buffer full */
            beep();
        }
        else {
            /* Caractere ordinaire - ordinary character */
            *p++ = c; *p = 0;
            putchar(c);
        }
    }
}

#ifdef TERMCAP
	static char term_buffer[2048];
	static char *term_clear_screen,
            *term_clear_line,
            *term_inv_video_on,
            *term_inv_video_off,
            *term_goto;
	static long  term_ok = 0;
#endif

#ifdef ENGLISH
# define MORE_MSG1  "Type [Return] or [Space] to continue, [q] to quit"
# define MORE_MSG2  "End of data, type [q] to quit"
#else
# define MORE_MSG1  "Tapez [Return] ou [Espace] pour continuer, [q] pour quitter"
# define MORE_MSG2  "Termine, tapez [q] pour quitter"
#endif

void more_init (const char *filename) {
    /*
     * On essaie d'ouvrir le fichier indique par 'filename' - we try to open file named 'filename'
     */
    more_fp = NULL;
    do_print = 1;           /* affichage actif - active display */

    if (filename && filename[0]) {
#ifdef P_OPEN
        if (filename[0] == '|') {
            is_pipe = 1;
            more_fp = popen(filename+1, more_opening_mode);
        } else
#endif
        {
            is_pipe = 0;
            more_fp = myfopen_in_subfolder(filename, more_opening_mode, subfolder_name, use_subfolder, 0);
        }
    }
    if (more_fp)  return; /* ok! */

    /*
     * Ca n'a pas reussi, il faut donc afficher sur l'ecran.
     * Nous supposons que init_ecran() a deja ete appele.
     *****
     * It didn't work, we must therefore display on screen.
     * We suppose init_screen() has already been called.
     */
    assert(nbrOfLines>0 && nbrOfColumns>0 && auto_wrap>=0);
    clearScreen();
    nbld = nbrOfLines-1;     /* nb de lignes disponibles - number of available lines */
    nble = 0;
}

void more_line (const char *line) {
    char c;
    long len, rest;

    if (line == NULL || do_print == 0)
        return;

    /* Ecriture dans un fichier ou un pipe - writing in a file or a pipe */
    if (more_fp) {
        fprintf(more_fp,"%s\n",line);
        return;
    }

    /*
     * Cas particulier: more_line("") equivaut a more_line(" ") quand il s'agit d'afficher sur l'ecran.
     *****
     * Special case: more_line("") is equivalent to more_line(" ") when displays on screen
     */
    if (line[0] == 0)
        line = " ";

    rest = strlen(line);
    while (rest > 0) {
        while (nbld <= 0) {
            reverse_video(MORE_MSG1);
            c = read_key();
            clear_line();
            switch(c) {
                case CCTL('C'):
                case 'q':
                case 'Q':
                    do_print = 0;
                    return;
                case 'd':
                case 'D':
                    nbld += (nbrOfLines-1)/2;
                    break;
                case ' ':
                    nbld += (nbrOfLines-1);
                    break;
                case '\r':
                case '\n':
                    ++nbld;
                    break;
                default:
                    beep();
            }
        }
        len = (rest < nbrOfColumns) ? rest : nbrOfColumns;
        fwrite(line, sizeof(char), len, stdout);
        if (!auto_wrap || len < nbrOfColumns)
            putchar('\n');
        line += len;
        rest -= len;
        --nbld;
        ++nble;
    }
}

void more_close (void) {
    char c;

    if (more_fp) {

#ifdef P_OPEN
        if (is_pipe)
            pclose(more_fp);
        else
#endif
        fclose(more_fp);
        return;
    }

    if (do_print) {
        /* Aller en bas de l'ecran si on n'y est pas deja - go to bottom of screen if we're not there already */
        screen_bottom();
        reverse_video(MORE_MSG2);
        do {
            c = read_key();
        } while (c != 'q' && c != 'Q' && c != 3);
    }
    clearScreen();
}

/*
 * Change le mode d'ouverture du prochain fichier avec more.
 * A utiliser en paire avec more_get_mode(). Usage typique :
 *
 *    strcpy(old_mode,more_get_mode());
 *    more_set_mode(new_mode);
 *    more_init(filename);
 *    ...
 *    more_line("blah blah");
 *    ...
 *    more_close();
 *    more_set_mode(old_mode);
 *
 * Cette pratique assure que more_opening_mode est laisse par defaut
 * a sa valeur standard "w".
 *
 *****
 *
 * Changes opening mode of more for next file.
 * To be used in pair with more_get_mode(). Typical usage:
 *
 *    strcpy(old_mode,more_get_mode());
 *    more_set_mode(new_mode);
 *    more_init(filename);
 *    ...
 *    more_line("blah blah");
 *    ...
 *    more_close();
 *    more_set_mode(old_mode);
 *
 * This way, we are sure that more_opening_mode is left by default to its
 * standard value 'w'.
 */
void more_set_mode(const char *mode) {
    strcpy(more_opening_mode, mode);
}

 /* Recupere le mode d'ouverture du prochain fichier avec more
  * Voir la discussion de more_set_mode().
  ****
  * Get opening mode of more for next file. See discussion in more_set_mode().
  */
char *more_get_mode(void) {
    return more_opening_mode;
}


/************************************************************************************+
|                                                                                    |
|   Les fonctions suivantes definissent l'interface avec le systeme d'exploitation.  |
|    Elles sont _tres_ dependantes de la machine!!!                                  |
|   Si vous voulez porter PAPP sur une autre machine, il est probable que c'est      |
|    la seule partie du programme qui necessitera des modifications importantes.     |
| *****                                                                              |
|   The following functions define interface with operation system.                  |
|    They are extremely machine-dependent                                            |
|   If you wish to port PAPP to another machine, this is probably the only part      |
|    of the program that will need major modifications                               |
|                                                                                    |
+************************************************************************************/

/*
 * void beep() : provoque un "beep" - plays a 'beep'
 */

void beep (void) {
    putchar(CCTL('G'));     /* ^G */
    fflush(stdout);
}

/*
 * void goodbye() : fait dire "goodbye" a l'ordinateur; sans effet si  vous ne voulez pas utiliser d'echantillons.
 *****
 * void goodbye(): make the computer say 'goodbye'
 */

void goodbye (void) {
}

/*
 * long put_lock(): verifier que PAPP n'est pas deja en train de s'executer dans le meme repertoire.
 * Renvoie 0 en cas de succes, 1 si le verrou est deja present, et -1 en cas d'erreur.
 *
 * void remove_lock(): enleve ce verrou. Attention, cette fonction sera
 * appelee meme si put_lock() a echoue!
 *
 ****
 *
 * long put_lock(): check if PAPP isn't already running in the same directory.
 * Returns 0 if success, 1 if lock is already present and -1 if error.
 *
 * void remove_lock(): remove lock. This function will be called even if put_lock() has failed!
 *
 ****
 * Changement par/Change by Stephane Nicolet le/the 28/03/2001 :
 * - meme sous UNIX, on implemente les verrous "a la main". Ceci explique le "if 0 && ..." qui suit.
 * - even under Unix, locks are hand implemented. that explains the "if 0 && ..." that follows
 */

#ifdef LOCK

# if 0 && (defined(UNIX_BSD) || defined(UNIX_SYSV))

long put_lock (void) {
    static char *lockfile = ".papp-lock";
    long fd, ret;

    if ((fd = open(lockfile, O_CREAT|O_RDWR, 0666)) < 0)
        return -1;

#  ifdef UNIX_BSD
    ret = flock(fd, LOCK_EX|LOCK_NB);
#  else
    {
        struct flock mylock;
        mylock.l_type   = F_WRLCK;
        mylock.l_whence = SEEK_SET;
        mylock.l_start  = 0;
        mylock.l_len    = 0;
        ret = fcntl(fd, F_SETLK, &mylock);
    }
#  endif
    if (ret < 0) {
        /* Fichier deja verrouille - file already locked */
        close(fd);
        return 1;
    }

    has_lock = 1;
    return 0;
}

void remove_lock (void) {
    /*
     * Le verrou sera automatiquement libere par le systeme d'exploitation lorsque le programme se terminera.
     ****
     * Lock will automaticaly be removed by OS when the program finishes.
     */
}

# else

/*
 * Verrouillage sur un systeme non-Unix, utilisant un fichier-verrou. Ce n'est pas totalement fiable
 * (il y a une race-condition evidente) et en cas de plantage grave, il se peut que le verrou ne soit pas
 * libere. Je pense que c'est tout de meme mieux que rien...
 *
 ****
 *
 * Non-Unix (see note underneath) locking mechanism, using a lockfile. It is not completely reliable
 * (obvious race condition) and in case of crash, lockfile may not be freed. Better than nothing...
 *
 ****
 * Changement par/Change by Stephane Nicolet le/the 28/03/2001 :
 * cette methode est utilisee meme sur les systemes Unix - this method is used even on Unix systems
 */
static char *lockfile = "lockfile";
static long has_lock = 0;

long put_lock (void) {
    FILE *fp;

    if ((fp = myfopen_in_subfolder(lockfile, "r", "", 0, 0)) != NULL) {
        /* verrou deja present - lock already present */
        fclose(fp);
        return 1;
    }
    if ((fp = myfopen_in_subfolder(lockfile, "w", "", 0, 0)) == NULL) {
        /* impossible de creer le verrou - impossible to create lock */
        return -1;
    }
    fclose(fp);
    has_lock = 1;
    return 0;
}

void remove_lock (void) {
    if (has_lock) {
        remove(lockfile);
        has_lock = 0;
    }
}

# endif
#else

/* Pas de verrouillage du tout - no lock at all */
long put_lock(void)  { return 0; }
void remove_lock(void)  { }

#endif

/*
 * void init_screen() : initialise les variables nbrOfLines et nbrOfColumns, et le drapeau auto-wrap
 * (si le curseur passe automatiquement a la ligne suivante quand on depasse la derniere colonne,
 * ce drapeau doit valoir un; et sinon, zero).
 *
 * NOTE: sous Unix cette fonction peut etre appelee plusieurs fois,
 * si vous suspendez puis relancez le programme.
 *
 ****
 *
 * void init_screen(): nbrOfLines and nbrOfColumns variables are initialized. and auto-wrap flag
 * (if cursor automatically wraps to next line when last column is exceeded, value of this flag must
 * be 1, 0 otherwise).
 *
 * NOTE: this function may be called several times under Unix, if program is suspended and awaken.
 */

void init_screen(void) {
#ifdef TERMCAP
    /*
     * Terminfo n'est pas supporte, desole... - Sorry Terminfo isn't supported...
     */
# ifdef ENGLISH
#  define TCAP_ERR1 "can't open termcap file"
#  define TCAP_ERR2 "unknown terminal type"
#  define TCAP_ERR3 "terminal is too dumb"
# else
#  define TCAP_ERR1 "impossible d'ouvrir le fichier termcap"
#  define TCAP_ERR2 "type de terminal inconnu"
#  define TCAP_ERR3 "terminal trop bete"
# endif
    char *el, *ec;
    struct winsize screen;
    char *term_name, *tbuf2;

    /*
     * Le type de terminal est-il deja connu? C'est tout a fait possible, si le programme a ete suspendu
     * puis relance ; dans ce cas, nous avons juste a verifier les dimensions de l'ecran.
     ****
     * Is terminal type already known? It may be possible if program has been suspended and awaken;
     * in that case, only screen size has to be checked.
     */
    if (term_ok)
        goto screen_size;

    if ((term_name = getenv("TERM")) == NULL)
        term_name = "dialup";
    term_ok = tgetent (term_buffer, term_name);
    if (term_ok < 0)
        fatal_error(TCAP_ERR1);
    if (term_ok == 0)
        fatal_error(TCAP_ERR2);
    CALLOC(tbuf2, sizeof(term_buffer), char);

    /*
     * On recupere les differentes sequences d'echappement - escape sequences are gathered
     */

    /* Effacement de l'ecran - screen clearing */
    term_clear_screen = tgetstr("cl", &tbuf2);

    /* Effacement du reste de la ligne - rest of line clearing */
    term_clear_line = tgetstr("ce", &tbuf2);

    /* Passage en video inverse, retour en video normale - inverse video and back to normal */
    term_inv_video_on  = tgetstr("so", &tbuf2);
    term_inv_video_off = tgetstr("se", &tbuf2);

    /* Chaine de positionnement - string for positionning */
    term_goto = tgetstr("cm", &tbuf2);

    if (!term_clear_screen || !term_clear_line || !term_goto)
        fatal_error(TCAP_ERR3);

    /* Le terminal est-il auto-wrap? - is terminal auto-wrap? */
    auto_wrap = tgetflag("am");

screen_size:
    /* Combien l'ecran a-t-il de lignes et de colonnes? - how many lines and columns? */
    if (ioctl(1, TIOCGWINSZ, &screen) == 0) {
        nbrOfLines   = screen.ws_row;
        nbrOfColumns = screen.ws_col;
/*      printf(" ** %ld lignes, %ld colonnes **\n", nbrOfLines, nbrOfColumns) ;
        getchar() ;*/
    } else {
        nbrOfLines = nbrOfColumns = 0;
    }

    /* Les variables d'environnement ont priorite - environment variables have priority */
    if ((el = getenv("LINES")) != NULL)
        nbrOfLines = atoi(el);
    if ((ec = getenv("COLUMNS")) != NULL)
        nbrOfColumns = atoi(ec);

    /* Dernier recours: les valeurs donnees par termcap - last resort, values given by termcap */
    if (nbrOfLines == 0)
        nbrOfLines = tgetnum("li");
    if (nbrOfColumns == 0)
        nbrOfColumns = tgetnum("co");

#elif defined(atarist)

    struct winsize screen;

    printf("\033v\033e\033q");
    fflush(stdout);
    auto_wrap = 1;
    ioctl(1, TIOCGWINSZ, &ecran);   /* Utilise la ligne-A - use line-A */
    nbrOfLines   = screen.ws_row;
    nbrOfColumns = screen.ws_col;

#elif defined(__MSDOS__)

   #ifdef __GO32__
       gppconio_init();
   #elif defined( __BORLANDC__ )
   #else
      window (1,1,80,25);
      _setcursortype (_NORMALCURSOR);
      textattr(7);
   #endif

    auto_wrap   =  1;
    nbrOfLines   = 25;
    nbrOfColumns = 80;

#elif defined(__THINK_C__)

    cinverse(TRUE,stdout);  /* Autorise la video inverse - enables reverse video */
    auto_wrap   =  1;
    nbrOfLines   = 25;
    nbrOfColumns = 80;

#elif defined(PAPP_MAC_METROWERKS)

    auto_wrap   =  1;
    nbrOfColumns  = SIOUXSettings.columns    ;
    nbrOfLines    = SIOUXSettings.rows       ;

#elif defined(CYGWIN)
{
	struct winsize screen;
    if (ioctl(1, TIOCGWINSZ, &screen) == 0) {
        nbrOfLines   = screen.ws_row;
        nbrOfColumns = screen.ws_col;
/*      printf(" ** %ld lignes, %ld colonnes **\n", nbrOfLines, nbrOfColumns) ;
        getchar() ; */
    } else {
        nbrOfLines = nbrOfColumns = 0;
    }
}

	auto_wrap = 1;
#else

    auto_wrap   =  1;
    nbrOfLines   = 25;
    nbrOfColumns = 80;

#endif

    assert(nbrOfLines>0 && nbrOfColumns>0 && auto_wrap>=0);
}

/*
 * void screen_reset() : retablit l'etat initial du terminal - reset terminal to initial state
 */

void screen_reset(void) {
    /* Rien pour l'instant - nothing here for the moment */
    fflush(stdout);
}

/*
 * void init_keyboard() : initialise le clavier. Si le programme est
 * suspendu puis relance, cette fonction sera appelee plusieurs fois.
 *
 * void keyboard_reset(): retablit l'etat initial du clavier.
 ****
 * void init_keyboard(): initialize keyboard. This function may be called several times,
 * if program is suspended and awaken.
 *
 * void keyboard_reset(): reset keyboard initial state.
 */

#if defined(UNIX_BSD) || defined(UNIX_SYSV)

static struct termios otty;

void init_keyboard(void) {
    struct termios ntty;

    tcgetattr(0, &otty);
    tcgetattr(0, &ntty);
    ntty.c_iflag &= ~( INPCK | ISTRIP | INLCR | IGNCR | ICRNL
        | IXON | IXOFF );
    ntty.c_lflag &= ~( ECHO | ECHOE | ECHOK | ECHONL | ICANON | ISIG );
    ntty.c_cc[VMIN] = 1;
    ntty.c_cc[VTIME] = 0;
    tcsetattr(0, TCSANOW, &ntty);
}

void keyboard_reset(void) {
    tcsetattr(0, TCSANOW, &otty);
}

#elif defined(__THINK_C__)

void init_keyboard(void) { csetmode(C_RAW,stdin); }
void keyboard_reset(void)    { }

#elif defined(PAPP_MAC_METROWERKS)

void init_keyboard(void) { FlushEvents(everyEvent,0); }
void keyboard_reset(void)    { }

#else

void init_keyboard(void) { }
void keyboard_reset(void)    { }

#endif

/*
 * long read_key() : attend un caractere au clavier, et renvoie son code Ascii. Cette fonction ne doit pas
 * envoyer d'echo a l'ecran. Les caracteres comme ^C ne doivent pas etre consideres comme speciaux.
 * Cette fonction doit auparavant vider le buffer de stdout.
 ****
 * long read_key(): waits for a keyboard character and returns its ascii code. This function must not
 * send echo on screen. Characters as ^C must not be considered special.
 * This function must first empty stdout buffer.
 */

long read_key(void) {
#if defined(UNIX_SYSV) || defined(UNIX_BSD)

    long c;
    fflush(stdout);
    if ((c = getchar()) == EOF)
        raise(SIGHUP);
    return (unsigned char)c;

#elif defined(PAPP_MINGW)

    fflush(stdout);
    return (unsigned char)_getch();

#elif defined(__GO32__)

    fflush(stdout);
    return (unsigned char) getkey();

#elif defined(PAPP_WINDOWS_METROWERKS)

    long c;
    fflush(stdout);
    fflush(stderr);
    while ((c = getchar()) == EOF)
        ;
    return (unsigned char)c;

#elif defined(__MSDOS__)

  #if defined(__BORLANDC__)

    fflush(stdout);
    return (unsigned char) getch();

  #else

    fflush(stdout);
    return (unsigned char) bioskey(0);

  #endif

#elif defined(atarist)

    fflush(stdout);
    return (unsigned char) Crawcin();

#elif defined(__THINK_C__)

    long c;
    fflush(stdout);
    fflush(stderr);
    while ((c = getchar()) == EOF)
        ;
    return (unsigned char)c;

#elif defined(PAPP_MAC_METROWERKS)

    long c;
    Boolean done = FALSE;
    EventRecord theEvent;
    CursorDevicePtr  theCursorDevice = nil;
    static long aux = 2;

    fflush(stdout);
    if (WaitNextEvent(updateMask, &theEvent, 1, nil))
        SIOUXHandleOneEvent(&theEvent);

    while (!done) {
        if (WaitNextEvent(everyEvent, &theEvent, 1, nil)) {
            if ((theEvent.what == keyDown) || (theEvent.what == autoKey)){
                c = theEvent.message & charCodeMask;
                done = TRUE;
            } else
                SIOUXHandleOneEvent(&theEvent);
        } else
        SystemTask();
    }
    fflush(stdout);

    /* on bouge un peu le curseur de la souris - let's move mouse cursor */
    if (NGetTrapAddress(0xAADB, ToolTrap) != NGetTrapAddress(_Unimplemented, ToolTrap)) {
        do {CursorDeviceNextDevice(&theCursorDevice);}
        while ((theCursorDevice != nil) && (*theCursorDevice).cntButtons < 1);
        if (nil != theCursorDevice)
            CursorDeviceMove(theCursorDevice,0,(aux = -aux));
    }

    return (unsigned char)c;

#endif
    return EOF;
}

/*
 * long getRandom(long n) : renvoie un entier aleatoire entre 0 et n-1.
 ****
 * long getRandom(long n): returns random number between 0 and n-1.
 */

long getRandom (long n) {
    static long initialized = 0; /* drapeau de premiere fois - first call flag */

    assert(n > 0);          /* precondition */

    if (!initialized) {
        /* Initialisation du generateur de nombres aleatoires - random seed initialization */
        srandom(time(NULL));
        initialized = 1;
    }
    return (random() / 5) % n;
}

/*
 * Les fonctions suivantes concernent la gestion du terminal :
 *
 * void clear_screen(): efface l'ecran et se place au coin superieur gauche
 * void clear_line(): efface la ligne courante et se place au debut de celle-ci
 * void screen_bottom(): place le curseur au coin inferieur gauche
 * void reverse_video(const char *): affiche une chaine en video inverse
 *
 * Remarque: si ces fonctions ne passent pas par <stdio.h>, elles doivent
 * d'abord vider le tampon de stdout, pour eviter (par exemple) qu'apres un
 * effacement d'ecran il reste des caracteres en attente...
 *
 * ***
 *
 * The following functions are about terminal management
 *
 * void clearScreen(): clear screen and go to upper left corner
 * void clear_line(): clear current line and go to the start of it
 * void screen_bottom(): put cursor in lower left corner
 * void reverse_video(const char *): display a string with reverse video (white on black)
 */

void clearScreen(void) {
#ifdef MACOSX
    putp(term_clear_screen) ;

#elif defined CYGWIN

    printf("%s", "\E[H\E[J");

#elif defined(TERMCAP)

    printf("%s", term_clear_screen);

#elif defined(atarist)

    printf("\033E");

#elif defined(PAPP_WINDOWS_METROWERKS)

    printf(" ");
    fflush(stdout);
    clrscr();

#elif defined(__MSDOS__)

    fflush(stdout);
    gotoxy(1,1);
    clrscr();

#elif defined(__THINK_C__)

    cgotoxy(1,1,stdout);
    ccleos(stdout);

#elif defined(PAPP_MAC_METROWERKS)

   /*  La ligne suivante est necessaire pour pouvoir appeler clear_screen() sur une console vide
    *  a cause d'un BUG dans la bibliotheque SIOUX de Metrowerks.
    ****
    * Following line is necessary to be able to call clearScreen() on an empty console
    * because of a bug in Metrowerks SIOUX library
    */
   printf(" ");
   fflush(stdout);
   SIOUXDoEditSelectAll();
   SIOUXDoEditClear();

#else /* defaut */

    fflush(stdout);

#endif
}

void clear_line(void) {
#ifdef MACOSX

    printf("\r");
    putp(term_clear_line) ;

#elif defined CYGWIN

    printf("\r%s", "\E[K");

#elif defined(TERMCAP)

    printf("\r%s", term_clear_line);

#elif defined(atarist)

    printf("\r\033l");

#elif defined(__MSDOS__)

    #ifdef __BORLANDC__

    gotoxy(1,wherey());
    clreol();

    #elif

    putchar('\r');
    fflush(stdout);
    delline();

    #endif

#elif defined(__THINK_C__)

    long x,y;

    cgetxy(&x,&y,stdout);
    cgotoxy(1,y,stdout);
    ccleol(stdout);

#elif defined(PAPP_MAC_METROWERKS)

    long i;
    char white_line[500];

    for (i = 0; i<nbrOfColumns; i++)
       white_line[i] = ' ';
    white_line[nbrOfColumns] = '\0';

    /* on se place a la fin du texte - go to end of texte */
#if SIOUX_USE_WASTE
    WESetSelection(LONG_MAX, LONG_MAX, SIOUXTextWindow->edit);
#else
    TESetSelect(32767, 32767, SIOUXTextWindow->edit);
#endif /* SIOUX_USE_WASTE */

    putchar('\r');
    printf("%s",white_line);
    putchar('\r');

#endif
}

void screen_bottom(void) {
#ifdef MACOSX

    putp(tgoto(term_goto, 0, nbrOfLines-1)) ;

#elif defined CYGWIN

	printf("\E[%ld;%dH", nbrOfLines , 1);

#elif defined(TERMCAP)

    printf("%s", tgoto(term_goto, 0, nbrOfLines-1));

#elif defined(atarist)

    printf("\033Y%c%c", 32 + (nbrOfLines-1), 32);

#elif defined(PAPP_WINDOWS_METROWERKS)

    long k;
    for (k=0 ; k < (nbrOfLines - nble - 1) ; k++)
        putchar('\n');
    fflush(stdout);

#elif defined(__MSDOS__)

    fflush(stdout);
    gotoxy(1, nbrOfLines);

#elif defined(__THINK_C__)

    cgotoxy(1,nbrOfLines,stdout);

#elif defined(PAPP_MAC_METROWERKS)

    long k;
    for (k=0 ; k < (nbrOfLines - nble - 1) ; k++)
        putchar('\n');
    fflush(stdout);

#endif
}

void reverse_video (const char *string) {
#ifdef MACOSX

    putp((term_inv_video_on ? term_inv_video_on : "")) ;
    printf("%s", string) ;
    putp((term_inv_video_off ? term_inv_video_off : "")) ;

#elif defined CYGWIN

    printf("\E[7m");
	printf("%s", string);
    printf("\E[27m");



#elif defined(TERMCAP)

    printf("%s%s%s", (term_inv_video_on ? term_inv_video_on : ""),
        string, (term_inv_video_off ? term_inv_video_off : "") );

#elif defined(atarist)

    printf("\033p%s\033q", string);

#elif defined(__MSDOS__) && !defined(__GO32__) && !defined(__BORLANDC__)

    fflush(stdout);
    textattr(112);
    printf("%s", string);
    fflush(stdout);
    textattr(7);

#elif defined(__THINK_C__)

    char c;
    while ((c = *string++))
        putchar(c | 0x80);

#elif defined(PAPP_MAC_METROWERKS)

    printf("%s ", string);
    fflush(stdout);

    /* on selectionne la longueur de la chaine  - select string length */
#if SIOUX_USE_WASTE
    SInt32 weSelStart, weSelEnd;
    WEGetSelection( &weSelStart, &weSelEnd, SIOUXTextWindow->edit );
    WESetSelection( weSelEnd - strlen(string) - 1 , weSelEnd - 1, SIOUXTextWindow->edit );
#else
    TESetSelect( (*SIOUXTextWindow->edit)->selEnd - strlen(string) - 1,
                 (*SIOUXTextWindow->edit)->selEnd - 1, SIOUXTextWindow->edit);
#endif /* SIOUX_USE_WASTE */

#else

    /* La video inverse du pauvre - poor man's reverse video */
    printf("-- %s --", string);

#endif
}



/* long print_file(char *filename)
 *
 * Envoie le fichier *filename du repertoire courant sur l'imprimante. Le fichier est suppose ferme au depart,
 * et rendu ferme. Il faut renvoyer 0 si tout se passe bien, ou -1 en cas de probleme
 * (imprimante non trouvee, fichier non trouve, etc).
 ****
 * Sends the given filename of the current directory to the printer. File is considered closed at start and
 * will be closed at the end. 0 must be returned if all goes well, -1 in case of error
 * (printer or file not found...)
 */
long print_file(char *filename) {

# ifdef ENGLISH
#  define IMPR_FICHIER  "\nprinting file %s ...\n\n"
#  define IMPR_ERROR    "\ncan't print file %s !?\n\n"
# else
#  define IMPR_FICHIER  "\nimpression du fichier %s ... \n\n"
#  define IMPR_ERROR    "\nimpossible d'imprimer le fichier %s !?\n\n"
# endif


#if defined(__THINK_C__)

    FILE *hidden_console, *whichfile;
    char c;

    hidden_console = fopenc();
    if (hidden_console == NULL) return(-1);
    chide(hidden_console);
    whichfile = fopen(filename,"r");
    if (whichfile == NULL) return(-1);
    printf(IMPR_FICHIER,filename);
    cecho2printer(hidden_console);
    while ( (c = fgetc(whichfile)) != EOF )
        putc(c, hidden_console);
    fclose( hidden_console );
    fclose( whichfile );
    if ((ferror(hidden_console) != 0) || (ferror(whichfile) != 0)
      return(-1);
    else
      return 0;

#elif defined(PAPP_MAC_METROWERKS)

    FILE *whichfile;
    GrafPtr savedPort;
    TPrStatus   prStatus;
    TPPrPort    printPort;
    THPrint hPrint;
    Boolean ok;
    Boolean doneDrawingPagesOfTextFile;
    static Boolean  DoPageSetUp  = TRUE;
    short   i;
    short numOfLinesPerPage;
    Str255  aStringOfText;



    GetPort(&savedPort);
    PrOpen();
    hPrint = (THPrint)NewHandle(sizeof(TPrint));/* *not* sizeof(THPrint) */
    PrintDefault(hPrint);
    if (DoPageSetUp) {
        ok = PrStlDialog(hPrint);
        DoPageSetUp = FALSE;
    }
    else {
        PrValidate(hPrint);
        ok = TRUE;
    }
    /* ok = PrJobDialog(hPrint); */

    if (ok)
    {
        printPort = PrOpenDoc(hPrint, nil, nil);
        SetPort(&printPort->gPort);
        TextSize(10);
        TextFont(courier);

        whichfile = fopen(filename,"r");
        if (whichfile != NULL)  {
            printf(IMPR_FICHIER,filename);

            doneDrawingPagesOfTextFile = FALSE;
            while (!doneDrawingPagesOfTextFile)
            {
                PrOpenPage(printPort, nil); /* Open a new printing page. */
                numOfLinesPerPage = (printPort->gPort.portRect.bottom - printPort->gPort.portRect.top)/14;

                for (i = 1; i <= numOfLinesPerPage; ++i) {
                    fgets((char *)aStringOfText, 255, whichfile);
                    if (feof(whichfile))
                        doneDrawingPagesOfTextFile = TRUE;
                    else {
                        CtoPstr((char *)aStringOfText);
                        /*
                         * Carriage return characters mess up DrawString, so they are removed.
                         * Also, tabs are not handled correctly.  They are left as an exercise
                         * for the programmer!
                         */
                        if (aStringOfText[aStringOfText[0]] == '\n')
                                aStringOfText[aStringOfText[0]] = ' ';
                        MoveTo(10, 14 * i);
                        DrawString(aStringOfText);
                    }
                }
                PrClosePage(printPort); /* Close the currently open page. */
            }

            fclose( whichfile );
        }
        else
          ok = FALSE;

        PrCloseDoc(printPort);
        /* Print spooled document, if any. */
        if ((**hPrint).prJob.bJDocLoop == bSpoolLoop && PrError() == noErr)
            PrPicFile(hPrint, nil, nil, nil, &prStatus);
    }

    DisposeHandle((char **)hPrint);
    PrClose();
    SetPort(savedPort);

    if (!ok) {
        printf(IMPR_ERROR,filename);
        return(-1);
    }
    else
        return 0;

#elif defined(UNIX_BSD) || defined(UNIX_SYSV)

    char s[512];

    sprintf(s, "lpr < %s", filename);
    if (system(s) == -1)
        return -1;
    return(0);

#else

   /* Garder ca par defaut tant qu'on n'a pas implemente l'impression pour sa machine :-)
    ****
    * Keep this as default as long as printing is not implemented for our machine :)
    */
   printf(IMPR_ERROR,filename);
   beep();
   read_key();
   return(-1);

#endif
}

/* long isFIleExist(char *filename)
 *
 * Teste l'existence d'un fichier. Le fichier est suppose ferme au depart, et rendu ferme.
 * Renvoie 1 si le fichier existe, et 0 sinon.
 ****
 * Check whether a file exists. File is considered closed at start and will be closed at the end.
 * Returns 1 if file exists and 0 otherwise.
 */
long isFIleExist(char *filename) {
    FILE *fp = myfopen_in_subfolder(filename, "r", subfolder_name, use_subfolder, 0);
    if (fp == NULL)
        return (0);
    else {
        fclose(fp);
        return (1);
    }
}

#if defined(PAPP_MAC_METROWERKS)

void init_mac_SIOUX_console(void) {
WindowPtr theWP;

    SIOUXSettings.columns = 81;
    SIOUXSettings.rows    = 45;    /* a priori on demande 45 lignes - ask for 45 lines*/
    SIOUXSettings.tabspaces  = 0;
    SIOUXSettings.setupmenus = TRUE;
    SIOUXSettings.standalone = TRUE;
    SIOUXSettings.showstatusline  = FALSE;
    SIOUXSettings.autocloseonquit = TRUE;
    SIOUXSettings.asktosaveonclose = FALSE;

    /* Il peut arriver qu'une fenetre de 45 lignes ne tienne pas sur un petit ecran,
     * en particulier sur les portables. On calcule donc le vrai nombre de lignes disponibles sur l'ecran :
     * pour cela, on ouvre la fenetre SIOUX, puis on divise sa hauteur par la hauteur
       des caracteres (valable pour la police Monaco 9) */
    printf("\n");
    theWP = (WindowPtr)SIOUXTextWindow;
    SIOUXSettings.rows    = (theWP->portRect.bottom - theWP->portRect.top - 12) / 11;
}

#endif
