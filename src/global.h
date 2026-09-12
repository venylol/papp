/* global.h
 *
 * - definitions diverses
 * - various definitions
 *
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, add two function prototypes for xml saves
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 30/03/2007 : Remove 'CONCAT()' macro and turn it into function.
 * (EL) 06/02/2007 : Add 'copy_files()' function
 * (EL) 05/02/2007 : Add 'tournament_name' and 'number_of_rounds' variables
 * (EL) 04/02/2007 : Add two variables to save type of config file and work file
                     (old or new version)
 * (EL) 02/02/2007 : change Brightwell coefficient type to 'double'
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */

#ifndef __Global_h__
#define __Global_h__

#include <stdio.h>
#include <stdlib.h>
#include "pairings.h"
#include "changes.h"
#include "more.h"
#include "discs.h"

/*
 *  global.c and pap.l prototypes
 */

#define CONFIG_F   300
#define PLAYERS_F  301
#define NEWPLAYERS_F 302
long read_file (char *filename, long type);
void syntax_error(const char *erreur);
void syntax_warning (const char *erreur);

void block_signals(void), unblock_signals(void);
void install_signals(void);
void save_round(void);
void save_registered(void);
void save_pairings(void);
void save_tournament_infos(void);
void recreate_workfile(void);
void big_dump(FILE *fp);
void init_default_penalties(void);
void check_penalties(void);
long number_of_digits(long n);
char *trivialloc (long length);
void COPY(const char *source, char **dest) ;
void CONCAT(const char *source, const char *added, char **dest) ;
char *fscore(discs_t score);

/* This functions terminate the program -
 * Les fonctions suivantes terminent le programme */
#ifdef __GNUC__
# define EXITING  __attribute__ ((noreturn))
/* #elif defined(__BORLANDC__) */
/* # define EXITING {} */
#else
# define EXITING
#endif

void fatal_error(const char *error)  EXITING;
void terminate(void)         EXITING;

/*
 *  penalties.c prototypes
 */

void    compute_pairings(void);

/*
 *  other prototypes
 */

void    tieBreak_computation(void);                 /* results.c  */
void    individualSheet(long, const char*);         /* results.c  */
void    create_ELO_file(void);                      /* elo.c      */
void    copy_nouveaux_file(FILE *fp_dest);          /* elo.c      */
int     yyparse(void);                              /* pap_tab.c  */

void    createXMLstandings(void);                   /* XML.c      */
void    createXMLround(void);                       /* XML.c      */


/*
 *  Macros for memory allocation
 */
#ifdef ENGLISH
# define ALLOC_FAILED   ": out of memory"
#else
# define ALLOC_FAILED   ": plus de memoire"
#endif

#define CALLOC(var,size,type)   do{             \
    var = (type*)malloc((size)*sizeof(type));           \
    if (!var)  fatal_error("malloc()" ALLOC_FAILED);  \
    } while(0)

#define REALLOC(var,size,type)  do{             \
    var = (type*)realloc(var,(size)*sizeof(type));      \
    if (!var)  fatal_error("realloc()" ALLOC_FAILED); \
    } while(0)


typedef int (*Sort_function)(const void*, const void*);
#define SORT(base,n,size,f) qsort((void*)base,n,size,(Sort_function)f)

/*
 *  Entry buffer size for terminal emulation -
 *  Taille du tampon d'entree pour l'emulation de terminal
 */

#define BUFFER_SIZE   70

/*
 *  Alloc-ring management
 *  Gestion de l'alloc-ring
 */

#define ALLOC_RING_LENGTH 256
#define ALLOC_RING_SLOTS  32    /* 32 temporary strings can be used before chaos.
                                 * On peut utiliser 32 chaines temporaires
                                 * avant de se tirer dans le pied */
extern long  alloc_ring_index;
extern char *alloc_ring[ALLOC_RING_SLOTS];
void init_alloc_ring (void);            /* Do not forget - Ne pas oublier!!!!! */
char *new_string (void);

/*
 *  Global variables
 */

#define NMAX_ROUNDS     128

/* Variables to deal with old configuration file and work file.
 * Variables pour gerer les anciens fichiers de configuration et intermediaire
 */

#define OLD        -1
#define NONEXISTING  0
#define CORRECT     1

extern long
        config_file_type,
        workfile_type ,
		new_players_file ; /* indicate if the new players file is being read.
                                * indique si c'est le fichier des nouveaux qu'on lit */

/* User defined parameter.
 * Parametres definis par l'utilisateur */

extern char
        *tournament_name,
        *subfolder_name,
		*tournament_date ;

extern long
        number_of_rounds ;

extern double
        brightwell_coeff ;

extern long
        current_round,
        immediate_save,
        result_file_save,
        standings_file_save,
        team_standings_file_save,
        pairings_file_save,
        html_crosstable_file_save,
        elo_file_save,
        use_subfolder,
		generate_xml_files,
        automatic_printing,
        print_copies,
        display_score_diff,
        discsTotal,
        scores_digits_number,
        floats_nmax,
        colors_nmax;

/* Non-interactive structured tournament calls use the same PAPP core. */
extern long papp_tournament_api;

extern discs_t
        bye_score;

/* User defined penalties
 * Penalites definies par l'utilisateur */

extern pen_t
        *color_penalty,
        *float_penalty,
        *country_penalty,
        same_colors_replay_penalty, opposite_colors_replay_penalty, immediate_replay_penalty,
        bye_replay_penalty, cumulative_floats_penalty, opposite_float_pen,
#ifdef ELITISM
        *elitism_penalty,
#endif
        repeated_color_penalty;

/* User defined strings and filenames
 * Chaines et noms de fichiers definis par l'utilisateur */

extern char
        *current_country,
        *default_country,
        *players_filename,
        *new_players_filename,
        *workfile_filename,
        *backup_workfile_filename,
        *log_filename,
        *config_filename,
        *results_filename,
        *standings_filename,
        *team_standings_filename,
        *pairings_filename,
        *HTML_crosstable_filename,
        *elo_filename,
        *nom_fichier_lu,
        *program_name,
        *color_1, *color_2;


#ifdef NO_RAISE
    #define raise(sig)  kill(getpid(),(sig))
#endif

/* utilities - utilitaires */
long max_of(long n1, long n2);
long min_of(long n1, long n2);
char *numbered_filename(char *pattern, long number);
long different_beginnings(const char *s1, const char *s2, long n);
long compare_strings_insensitive(const char *scan1, const char *scan2);
long copy_files(FILE *dest, FILE *source) ;
void backup_workfile(void) ;
void init_workfile(long file_type) ;
FILE *myfopen_in_subfolder(const char *filename, const char *mode, const char *folder, long used, long err) ;

/*
 * Variables and prototypes from roundrobin.c. Functions return 1
 * in case of success and 0 otherwise.
 * Variables et prototypes de roundrobin.c. Les fonctions renvoient 1 en
 * cas de succes et 0 en cas d'echec.
 */

extern long minPlayers4rr, maxPlayers4rr;

long save_rrTable(FILE *fp);
long load_rrTable(long card, long *table);
long initRoundRobin(void);
long roundRobin_pairings(void);



#endif    /*  #ifndef __Global_h__   */
