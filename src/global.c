/*
 * global.c: fonctions qu'on ne sait pas ou mettre
 *
 * (EL) 19/02/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, Ajout de la variable 'generate_xml_files' indiquant
                     s'il faut generer des fichiers xml en sortie.
                     Ajout du drapeau _POSIX_C_SOURCE pour permettre la compilation
                     avec les options -ansi et -pedantic sur Linux.
 * (EL) 20/07/2008 : v1.33, On sauve la date du tournoi dans le fichier intermediaire.
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 06/04/2007 : Ecriture de 'myfopen_in_subfolder()' qui essaie de creer le
                     sous-dossier avant d'ouvrir un fichier.
 * (EL) 05/04/2007 : Ecriture de la fonction 'init_fichier_intermediaire' qui initialise
                     un fichier intermediaire (en recopiant evnt un fichier a l'ancien format)
 * (EL) 05/04/2007 : Ecriture de la fonction 'backup_inter()' qui effectue une sauvegarde
                     du fichier intermediaire dans un sous-dossier si on l'utilise.
 * (EL) 05/04/2007 : Ajout de la variable 'backup_workfile_filename' permettant de stocker
                     le fullname du fichier intermediaire AVEC le fullname du sous-dossier pour la
                     sauvegarde dans ce sous-dossier.
 * (EL) 30/03/2007 : Tranformation de 'COPIER()' en fonction et changement de son code
                     ainsi que des parametres.
                     Ecriture de 'CONCAT()' permettant de concatener 2 chaines.
 * (EL) 30/03/2007 : Ajout des variables 'subfolder_name' et 'use_subfolder'
                     qui servent a regrouper tous les fichiers ronde/class/result...
                     dans un sous-dossier portant le fullname du tournoi.
 * (EL) 29/03/2007 : Reecriture de la fonction 'grand_dump()' pour coller avec
                     le nouveau format du fichier de configuration.
 * (EL) 06/02/2007 : Ajout de la fonction 'copier_fichier()'
 * (EL) 05/02/2007 : Ajout des variables 'tournament_name' et 'number_of_rounds'
 * (EL) 04/02/2007 : Ajout de deux variables pour memoriser le type des fichiers
                     de config et intermediaire (ancienne ou nouvelle version)
 * (EL) 02/02/2007 : changement du type du coefficient de Brightwell en double.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * global.c: functions we don't have anywhere else to put!
 *
 * (EL) 19/02/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, Add variable 'generate_xml_files' indicating
                     if xml files should be generated.
                     Add _POSIX_C_SOURCE flag to enable compilation with
                     -ansi and -pedantic options on Linux.
 * (EL) 20/07/2008 : v1.33, tournament date is saved in intermediate file
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 06/04/2007 : Function 'myfopen_in_subfolder()' tries to create
                     subfolder before writing a file.
 * (EL) 05/04/2007 : Function 'init_workfile' which initializes
                     an intermediate file (and eventually copies an old format file)
 * (EL) 05/04/2007 : Function 'backup_workfile()' which makes a backup of the intermediate
                     file in a subfolder (if used).
 * (EL) 05/04/2007 : Add variable 'backup_workfile_filename' to save intermediate file full name
                     with subfolder fullname to save in that subfolder.
 * (EL) 30/03/2007 : Change 'COPY()' into a function; change code and parameters
                     'CONCAT()' function to concatenate two strings.
 * (EL) 30/03/2007 : Add variables 'subfolder_name' and 'use_subfolder'
                     which are used to save all files (rounds/results/standings...)
                     in a subfolder named after the tournament fullname.
 * (EL) 29/03/2007 : Rewriting of function 'big_dump()' to stick with the new format
                     for the configuration file.
 * (EL) 06/02/2007 : Add function 'copy_files()'
 * (EL) 05/02/2007 : Add variables 'tournament_name' et 'number_of_rounds'
 * (EL) 04/02/2007 : Add two variables to save configuration and intermediate file types
 *                   (old or new version)
 * (EL) 02/02/2007 : Change Brightwell coefficient to double.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#define  _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <assert.h>
#include <errno.h>

#include "pairings.h"
#include "global.h"
#include "couplage.h"
#include "more.h"
#include "version.h"

#ifdef atarist
# include <mintbind.h>
#endif

#if defined(UNIX_BSD) || defined(UNIX_SYSV)
    #include <sys/signal.h>
    #include <sys/ioctl.h>
    #include <unistd.h>
    #include <dirent.h>
    #include <sys/stat.h>
#endif

#if defined(CYGWIN) || defined(MACOSX) || defined(LINUX)
    #include <signal.h>
#endif

/* Variables pour gerer les anciens fichiers de configuration et intermediaire.
 ****
 * Variables to handle old config and intermediate files.
 */

long
    config_file_type,
    workfile_type,
	new_players_file ; /* indique si c'est le fichier des nouveaux qu'on lit - Is it new players file? */

char
    *tournament_name ="",
    *subfolder_name ="",
	*tournament_date = "" ;

long
    number_of_rounds = 5 ;

/* Penalites et tableaux de penalites elementaires - Penalties and elementary penalties array */

double
    brightwell_coeff ;
/* Le coeff de brightwell en flottant, 2 pts par victoire
 ****
 * Brighwell coeff as double, 2 points per victory */

long
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
    scores_digits_number,
    floats_nmax,
    colors_nmax;

long papp_tournament_api = 0;

discs_t
    bye_score;

pen_t
    *color_penalty,
    repeated_color_penalty,
    *float_penalty,
    *country_penalty,
    cumulative_floats_penalty,
    opposite_float_pen,
#ifdef ELITISM
    *elitism_penalty,
#endif
    same_colors_replay_penalty,
    opposite_colors_replay_penalty,
    immediate_replay_penalty,
    bye_replay_penalty;

char
    *default_country              = "online",
    *players_filename             = "joueurs",
    *new_players_filename         = "nouveaux.txt",
    *workfile_filename        = "papp-internal-workfile.txt",
    *backup_workfile_filename = "papp-internal-workfile-BACKUP.txt", /* backup dans le sous-dossier si necessaire */
    *log_filename                 = "papp.log",
    *config_filename              = "papp.cfg",
    *pairings_filename            = "round###.txt",
    *results_filename             = "stand###.txt",
    *standings_filename           = "stand###.txt",
    *team_standings_filename      = "team###.txt",
    *HTML_crosstable_filename     = "cross###.htm",
    *elo_filename                 = "papp.elo",
    *program_name,
#ifdef ENGLISH
    *color_1                 = "Black",
    *color_2                 = "White";
#else
    *color_1                 = "Noir",
    *color_2                 = "Blanc";
#endif


void init_default_penalties(void) {
    long i;

    /* Departage de Brightwell - Brightwell tiebreak */
    brightwell_coeff = 3.0; /* On compte 2 points par victoire - 2 pts per victory */

        /* Ne faire les sauvegardes que si necessaire - saves only if necessary */
    immediate_save = 1;
    result_file_save = 0;
    standings_file_save = 0;
    team_standings_file_save = 0;
    pairings_file_save = 0;
    html_crosstable_file_save = 0;
    elo_file_save = 0;
    use_subfolder = 1 ;
    generate_xml_files = 0 ;
    automatic_printing = 0;
    print_copies = 1;

    /* Afficher le nombre de pions, pas leur difference - print number of discs, not difference */
    display_score_diff = 0;

    /* Bip fait 24 pions sur 64 - Bye scores 24 discs of 64 */
    bye_score   = INTEGER_TO_SCORE(24);
    discsTotal = 64;
    scores_digits_number = number_of_digits(discsTotal);

    /*
     * Penalites de couleur par defaut - default color penalties
     */
    colors_nmax = 6;
    CALLOC(color_penalty, colors_nmax, pen_t);
    color_penalty[0] =     0;
    color_penalty[1] =    20;
    color_penalty[2] =   105;
    color_penalty[3] =   225;
    color_penalty[4] =   530;
    color_penalty[5] =  1075;
    repeated_color_penalty    =    15;

    /*
     * Penalites de flottement par defaut - default floats penalties
     */
    floats_nmax = 25;
    CALLOC(float_penalty, floats_nmax, pen_t);
    float_penalty [0] =           0;
    float_penalty [1] =       20000;
    float_penalty [2] =       40588;
    float_penalty [3] =       62785;
    float_penalty [4] =       87119;
    float_penalty [5] =      114041;
    float_penalty [6] =      143998;
    float_penalty [7] =      177453;
    float_penalty [8] =      214908;
    float_penalty [9] =      256910;
    float_penalty[10] =      304062;
    float_penalty[11] =      357037;
    float_penalty[12] =      416582;
    float_penalty[13] =      483534;
    float_penalty[14] =      558828;
    float_penalty[15] =      643514;
    float_penalty[16] =      738767;
    float_penalty[17] =      845906;
    float_penalty[18] =      966413;
    float_penalty[19] =     1101949;
    float_penalty[20] =     1254381;
    float_penalty[21] =     1425805;
    float_penalty[22] =     1618578;
    float_penalty[23] =     1835345;
    float_penalty[24] =     2079081;
    cumulative_floats_penalty          =          25;
    opposite_float_pen          =          10;

    /*
     * Les penalites de repetition par defaut sont infinies - repeated games penalties are inifinite
     */
    bye_replay_penalty  =  MAX_PEN;
    opposite_colors_replay_penalty    =  5000000;
    same_colors_replay_penalty   =  MAX_PEN;
    immediate_replay_penalty =        1;

    /*
     * Penalites de chauvinisme par defaut - default country penalties
     */
    CALLOC(country_penalty, NMAX_ROUNDS, pen_t);
    for (i = 0; i < NMAX_ROUNDS; i++)
            country_penalty[i] = 0;
    country_penalty[0] = 2;

    /*
     * Penalite d'elitisme par defaut - default elitism penalties
     */
#ifdef ELITISM
    CALLOC(elitism_penalty, NMAX_ROUNDS, pen_t);
    elitism_penalty[0] = 100;
    elitism_penalty[1] = 100;
    elitism_penalty[2] = 100;
    elitism_penalty[3] = 200;
    elitism_penalty[4] = 200;
    elitism_penalty[5] = 300;
    elitism_penalty[6] = 300;
    elitism_penalty[7] = 300;
    elitism_penalty[8] = 300;
        /* on veut favoriser les bons appariements pour
           le haut du classement vers la fin du tournoi
        ***
         Towards end of tournament, we want to favor good pairings for the
         top of the standings
         */
        for (i = 9; i < NMAX_ROUNDS; i++)
                elitism_penalty[i] = 400;
#endif

}

#ifdef ENGLISH
# define VPEN_COLORS    "non-increasing colour penalties"
# define VPEN_FLOAT     "non-increasing float penalties"
# define VPEN_MINFAC    "value of 'min_fac' is too big"
# define FATAL_OPEN     "ne peut etre ouvert (pb de droits ?) : "
# define FATAL_MSG      "%s: fatal error: %s\n"
# define FATAL_HAK      "Hit any key to quit."
#else
# define VPEN_COLORS    "penalites de couleur non croissantes"
# define VPEN_FLOAT     "penalites de flottement non croissantes"
# define VPEN_MINFAC    "la valeur de 'min_fac' est trop grande"
# define FATAL_OPEN     "ne peut etre ouvert (pb de droits ?) : "
# define FATAL_MSG      "%s: erreur fatale: %s\n"
# define FATAL_HAK      "Pressez une touche pour quitter."
#endif

void check_penalties(void) {
    long i;

    assert(colors_nmax >= 0 && floats_nmax >= 0);
    assert(color_penalty[0] == 0);
    assert(float_penalty[0] == 0);

    /* Monotonie des penalites de couleur - color penalties are monotonous */
    for (i = 0; i < colors_nmax-1; i++)
        if (color_penalty[i] > color_penalty[i+1])
            fatal_error(VPEN_COLORS);

    /* Penalites de flottement - floats penalties */
    if (2 * opposite_float_pen > float_penalty[1])
        fatal_error(VPEN_MINFAC);
    for (i = 0; i < floats_nmax-1; i++)
        if (float_penalty[i] > float_penalty[i+1])
            fatal_error(VPEN_FLOAT);
}

/*
 * Gestion des signaux - signals handling
 */
static void _ending(void) EXITING;    /* declaration forward */

#if defined(UNIX_BSD) || defined(UNIX_SYSV)
typedef void (*Signal_handler)(int);
static sigset_t old_sigmask;

#ifndef POSIX_SIGNALS
void bloquer_signaux(void) {
    old_sigmask = sigblock( (1L<<SIGHUP) | (1L<<SIGINT) |
                            (1L<<SIGQUIT) | (1L<<SIGTERM) );
}

void debloquer_signaux(void) {
    sigsetmask(old_sigmask);
}
#else
void block_signals(void) {
    sigset_t set;

    sigemptyset(&set);
    sigaddset(&set, SIGHUP);
    sigaddset(&set, SIGINT);
    sigaddset(&set, SIGQUIT);
    sigaddset(&set, SIGTERM);
    sigprocmask(SIG_BLOCK, &set, &old_sigmask);
}

void unblock_signals(void) {
    sigprocmask(SIG_SETMASK, &old_sigmask, NULL);
}
#endif

#ifndef POSIX_SIGNALS
    #define handle_signal(sig, fn)  signal((sig),(Signal_handler)(fn))
#else
    #define handle_signal(sig, fn)  do {           \
        struct sigaction act;                   \
        act.sa_handler = (Signal_handler)(fn);  \
        sigemptyset(&act.sa_mask);              \
        act.sa_flags = 0;                       \
        sigaction((sig), &act, NULL);           \
    } while(0)
#endif

/* changement par Stephane Nicolet, 27/05/2000. Je ne sais pas ou est defini winsize sur Unix,
 * tant pis on ne fera pas de changement de taille de fenetre
 ****
 * Change by Nicolet 27/05/2000. I don't know where winsize is on Unix.
 * We won't handle window size changes.
 */
static void change_size(void) {
    /*
     struct winsize screen;

     if (ioctl(1, TIOCGWINSZ, &screen) < 0)
     return;
     nbrOfLines   = screen.ws_row;
     nbrOfColumns = screen.ws_col;
# if defined(UNIX_SYSV) && !defined(POSIX_SIGNALS)
     handle_signal(SIGWINCH, change_size);
# endif
     */
}

void install_signals(void) {
    handle_signal(SIGHUP, _ending);
    handle_signal(SIGINT, SIG_IGN);
    handle_signal(SIGQUIT, terminate);
    handle_signal(SIGTERM, _ending);
    /*      handle_signal(SIGWINCH, change_size);    cf plus haut, SN */
}

#else

/* pas de signaux */
void block_signals(void)          { }
void unblock_signals(void)        { }
void install_signals(void)        { }

#endif

/*
 * Terminaison, normale ou anormale, de PAPP. - Papp exits, normal or not.
 */
void fatal_error(const char *error) {
    fprintf(stdout, "\n" FATAL_MSG,
            program_name[0] ? program_name : "Papp", error);
#if defined(__THINK_C__) || defined(PAPP_MAC_METROWERKS)
    /*
     * Attendre une pression de touche avant de quitter (afin de pouvoir
     * lire les messages d'erreur) - Wait key press to read error message
     */
    fprintf(stdout, FATAL_HAK);
    (void)lire_touche();
#endif

    remove_lock();
    keyboard_reset() ;
    screen_reset();
    exit(1);
}

void terminate(void) {
#if defined(UNIX_BSD) || defined(UNIX_SYSV)
    /* peut-etre ne pouvons-nous plus ecrire sur ce terminal - Maybe we can't write on terminal anymore */
    handle_signal(SIGTTIN, _ending);
    handle_signal(SIGTTOU, _ending);
#endif
#ifndef PAPP_MAC_METROWERKS
    screen_bottom();
    clear_line();
#endif
    clearScreen();
    fflush(stdout);
    keyboard_reset();
    screen_reset();
    goodbye();
    _ending();
}

static void _ending(void) {
    /* Nous devons sauvegarder l'etat - state must be saved */
    save_registered();
    save_pairings();
#ifdef atarist
    Psigreturn();
#endif
    remove_lock();
    exit(0);
}

/*
 * Les fonctions suivantes sont protegees contre les signaux - Following functions are signals protected
 */

void save_round(void) {
    block_signals();
    _save_round();
    unblock_signals();
}

void save_registered(void) {
    block_signals();
    _save_registered();
    unblock_signals();
}

void save_pairings(void) {
    block_signals();
    _save_pairings();
    unblock_signals();
}

void recreate_workfile(void) {
    block_signals();
    _recreate_workfile();
    unblock_signals();
}

char *fscore (discs_t score) {
    char *string;

    if (!SCORE_IS_LEGAL(score))
        return NULL;

    string = new_string();
    if (display_score_diff) {

        if (IS_VICTORY(score)) string[0] = '+';
        if (IS_DEFEAT(score))  string[0] = '-';
        if (IS_DRAW(score)) string[0] = '=';

        sprintf(string+1, "%s", discs2string(ABSOLUTE_VALUE_SCORE(RELATIVE_SCORE(score))));
    } else {
        sprintf(string, "%s/%s", discs2string(score), discs2string(OPPONENT_SCORE(score)));
    }
    return string;
}

/* fpen(): convertit une penalite en une chaine de sept caracteres
 ****
 * fpen(): converts a penalty into a seven chars string. */

static char *fpen (pen_t p) {
    static char buf[20];

    if (p >= MAX_PEN)
        return " INFINI";
    sprintf(buf, "%7ld", (long)p);
    return buf;
}

long number_of_digits(long n) {
    long k = 0;

    if (n == 0)
        return 1;
    else if (n < 0)
        ++k, n = -n;

    while(n)
        ++k, n /= 10;
    return k;
}




/* big_dump(): sauve les parametres courants dans le fichier pointe par fp.
 ****
 * big_dump(): save current parameters in file pointed to by fp.
 */

void big_dump(FILE *fp) {
    long i, l;
    char *buf;

    buf = new_string();

    fprintf(fp,
            "#! papp\n"
"#\n"
"# Ce fichier de configuration a ete cree automatiquement par PAPP parce\n"
"# qu'il n'y en avait aucun dans votre repertoire. Il indique quels sont\n"
"# les parametres pris par defaut. Libre a vous de modifier ces parametres\n"
"# pour adapter PAPP a vos besoins.\n\n"
                ) ;


    fprintf(fp,
"# Fichier de configuration pour Papp 1.34\n"
"# Configuration file for Papp 1.34\n"
"###############################################################################\n\n"
"###############################################################################\n"
"## Ces parametres vont aller dans le fichier \"papp-internal-workfile.txt\" et sont\n"
"## modifies a chaque nouveau tournoi.\n"
		   );
	fprintf(fp,
"## These parameters are now in the \"papp-internal-workfile.txt\" file and are modified\n"
"## for each new tournament.\n"
"##\n"
"##  Brightwell  = 3;                    # Coefficient de Brightwell\n"
"##\n"
		   );
	fprintf(fp,
"## On organise un toutes-rondes s'il y a entre 1 et 12 joueurs:\n"
"## A round-robin is organized if there are between 1 and 12 players:\n"
"##  Toutes-rondes = 1-12 joueurs;\n"
"###############################################################################\n\n\n"
"###############################################################################\n"
		   );
	fprintf(fp,
"###############################################################################\n"
"## Parametres que l'utilisateur peut vouloir changer:\n"
"## The user may want to change these parameters:\n\n"
            ) ;

    fprintf(fp,"# Pays par defaut / default country\n\tPays        = \"%s\";\n\n", default_country);
    fprintf(fp,"# Bip fait 24 pions sur 64 / BYE score and total number of discs\n"
            "\tScore-bip   = %s/%ld;\n\n", discs2string(bye_score), discsTotal);
    fprintf(fp,"# Utilisation d'un sous-dossier / use a folder for all files (true/false)\n"
            "\tdossier %s;\n\n", use_subfolder ? "true" : "false") ;
    fprintf(fp,"# Generation des fichiers XML en sortie / generate XML output files (true/false)\n"
            "\tXML %s;\n\n", generate_xml_files ? "true" : "false") ;
    fprintf(fp,"# Fichiers des appariements de chaque ronde / round pairings files\n"
            "\tFichier appariements   = \"%s\";\n\n", pairings_filename);
    fprintf(fp,"# Fichiers des resultats de chaque ronde / round results files\n"
            "\tFichier resultats      = \"%s\";\n\n", results_filename);
    fprintf(fp,"# Fichiers du classement de chaque ronde / round standings files\n"
            "\tFichier classement     = \"%s\";\n\n", standings_filename);
    fprintf(fp,"# Fichiers des tableaux HTML / HTML crosstables files\n"
            "\tFichier tableau-croise = \"%s\";\n\n", HTML_crosstable_filename);
    fprintf(fp,"# Fichiers resultats par equipes / team standings files\n"
            "#\tFichier equipes        = \"%s\";\n", team_standings_filename);
    fprintf(fp,
"###############################################################################\n"
"###############################################################################\n\n\n"
"###############################################################################\n"
"## Parametres normalement fixes.\n"
"## Parameters that normally should not be changed.\n\n"
"# Les fichiers utilises par Papp / files used by Papp\n"
            );
    sprintf(buf, "\tFichier joueurs        = \"%s\";", players_filename);
    fprintf(fp, "%-42s # Fichier principal des joueurs\n", buf);
    sprintf(buf, "\tFichier nouveaux       = \"%s\";", new_players_filename);
    fprintf(fp, "%-42s # Fichier des nouveaux joueurs\n", buf);
    sprintf(buf, "\tFichier inter          = \"%s\";", workfile_filename);
    fprintf(fp, "%-42s # Fichier intermediaire\n\n", buf);

    fprintf(fp,"# Actuellement inutilise / currently unused\n") ;
    sprintf(buf, "#\t Fichier elo           = \"%s\";", elo_filename);
    fprintf(fp, "%-42s # Fichiers Elo de chaque ronde\n", buf);
    sprintf(buf, "#\t Fichier log           = \"%s\";", log_filename);
    fprintf(fp, "%-42s # Fichier de log\n", buf);
    sprintf(buf, "#\t Fichier config        = \"%s\";", config_filename);
    fprintf(fp, "%-42s # Ce fichier meme...\n\n\n", buf);
    fprintf(fp, "# Frequence des sauvegardes / saves frequency\n\n"
            "\tSauvegarde %s;\n\n\n", immediate_save ? "immediate" : "differee");

    fprintf(fp, "# Type d'impressions des fichiers de resultats et de classement.\n");
    fprintf(fp, "# Deux types d'impression possibles : automatique NN si vous voulez\n");
    fprintf(fp, "# que PAPP imprime lui-meme NN copies des fichiers, manuelle si \n");
    fprintf(fp, "# vous preferez utiliser un editeur de texte.\n\n");
    fprintf(fp, "\tImpression %s;\n\n\n",automatic_printing ? "automatique 1" : "manuelle");
    fprintf(fp, "# Couleurs du premier et du second joueur\n\n"
            "\tCouleurs = { \"%s\", \"%s\" };\n\n\n",
            color_1, color_2 );
    fprintf(fp, "# Doit-on afficher les nombres de pions ou leur difference ?\n"
            "# Should we print absolute score or score difference?\n\n"
            "\tAffichage-pions %s;\n\n", display_score_diff ? "relatif" : "absolu" ) ;
    fprintf(fp,
            "# Zone d'insertion pour les nouveaux joueurs\n"
            "# Number ranges for new players\n"
			"\tzone-insertion \"ARG\" = 280000-289999;  # argentins\n"
			"\tzone-insertion \"AUS\" = 260000-269999;  # australiens\n"
			"\tzone-insertion \"A\"   = 310000-319999;  # autrichiens\n"
			"\tzone-insertion \"B\"   = 270000-279999;  # belges\n"
			"\tzone-insertion \"BLR\" = 360000-369999;  # bielorusses\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"BR\"  = 290000-299999;  # bresiliens\n"
			"\tzone-insertion \"BG\"  = 400000-409999;  # bulgares\n"
			"\tzone-insertion \"CDN\" = 110000-119999;  # canadiens\n"
			"\tzone-insertion \"CN\"  =  60000-69999;   # chinois\n"
			"\tzone-insertion \"CRO\" = 230000-234999;  # croates\n"
			"\tzone-insertion \"CZ\"  = 210000-219999;  # tcheques\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"D\"   =  70000-79999;   # allemands\n"
			"\tzone-insertion \"DK\"  = 220000-229999;  # danois\n"
			"\tzone-insertion \"E\"   =  90000-99999;   # espagnols\n"
			"\tzone-insertion \"F\"   =  50000-59999;   # francais\n"
			"\tzone-insertion \"FIN\" =  80000-89999;   # finlandais\n"
			"\tzone-insertion \"GR\"  = 300000-309999;  # grecs\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"GB\"  = 100000-109999;  # anglais\n"
			"\tzone-insertion \"HK\"  = 170000-179999;  # hong-kong\n"
			"\tzone-insertion \"I\"   =  40000-49999;   # italiens\n"
			"\tzone-insertion \"RI\"  = 180000-189999;  # indonesiens\n"
			"\tzone-insertion \"IL\"  = 200000-209999;  # israeliens\n"
			"\tzone-insertion \"IND\" = 350000-359999;  # indiens\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"IRA\" = 390000-399999;  # iraniens\n"
			"\tzone-insertion \"ISL\" = 330000-339999;  # islandais\n"
			"\tzone-insertion \"J\"   =  10000-19999;   # japonais\n"
			"\tzone-insertion \"MAL\" = 160000-169999;  # malais\n"
			"\tzone-insertion \"MEX\" = 340000-349999;  # mexicains\n"
			"\tzone-insertion \"N\"   = 140000-149999;  # norvegiens\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"NL\"  = 120000-129999;  # hollandais\n"
			"\tzone-insertion \"PL\"  = 130000-139999;  # polonais\n"
			"\tzone-insertion \"PK\"  = 380000-389999;  # pakistanais\n"
			"\tzone-insertion \"RO\"  = 370000-379999;  # roumains\n"
			"\tzone-insertion \"ROK\" =  30000-39999;   # sud-coreens\n"
			"\tzone-insertion \"RUS\" = 320000-329999;  # russes\n"
		   );
	fprintf(fp,
			"\tzone-insertion \"SRB\" = 235000-239999;  # serbes\n"
			"\tzone-insertion \"S\"   = 150000-159999;  # suedois\n"
			"\tzone-insertion \"CH\"  = 240000-249999;  # suisses\n"
			"\tzone-insertion \"SGP\" = 250000-259999;  # singapour\n"
			"\tzone-insertion \"T\"   = 190000-199999;  # thai\n"
			"\tzone-insertion \"USA\" =  20000-29999;   # americains\n"
			"\tzone-insertion \"PG\"  =   3200-3349;    # programmes\n"
			"\tzone-insertion         =   9000-9500;    # autres\n\n\n"
            );
    /*
     * Table des penalites - penalties table
     */

    fprintf(fp,
"# La table des penalites est divisee en cinq sections: couleur,\n"
"# flottement, repetition, chauvinisme, elitisme. Ces sections\n"
"# peuvent apparaitre dans un ordre quelconque. Si une section\n"
"# n'apparait pas, les valeurs par defaut des penalites de cette\n"
"# section sont conservees. Si elle apparait, toutes les penalites\n"
"# de la section sont initialisees a zero.\n\n"
            );
    fprintf(fp,"Penalites {\n");

    /*
     * Couleur - color
     */

    fprintf(fp,"\tCouleur:\n");
    for (i = 1; i < colors_nmax; i++) {
        sprintf(buf, "%ld", i);
        if (i == colors_nmax - 1)
            strcat(buf, "+");
        fprintf(fp,"\t\t%-3sfois  = %s;\n",
                buf, fpen(color_penalty[i]));
    }
    fprintf(fp,"\t\tde-suite = %s;\n", fpen(repeated_color_penalty));

    /*
     * Flottement - floats
     */

    fprintf(fp,"\n\tFlottement:\n");
    for (i = 1; i < floats_nmax; i++) {
        sprintf(buf, "%ld", i);
        if (i == floats_nmax - 1)
            strcat(buf, "+");
        fprintf(fp,"\t\t%-3sdemi-point%c  =     %s;\n",
                buf, i==1 ? ' ':'s', fpen(float_penalty[i]));
    }
    fprintf(fp,"\t\tde-suite        = %s;\n", fpen(cumulative_floats_penalty));
    fprintf(fp,"\t\tminoration      = %s;\n", fpen(opposite_float_pen));

    /*
     * Repetition - repeated games
     */

    fprintf(fp,"\n\tRepetition:\n");
    fprintf(fp,"\t\tbip-bip           = %s;\n", fpen(bye_replay_penalty));
    fprintf(fp,"\t\tcouleurs-opposees = %s;\n", fpen(opposite_colors_replay_penalty));
    fprintf(fp,"\t\tmemes-couleurs    = %s;\n", fpen(same_colors_replay_penalty));
    fprintf(fp,"\t\tde-suite          = %s;\n", fpen(immediate_replay_penalty));

    /*
     * Chauvinisme - country
     */

    for (l = i = NMAX_ROUNDS-1; l >= 0; l--)
        if (country_penalty[l] != country_penalty[i])
        { ++l; break; }

            fprintf(fp,"\n\tChauvinisme:\n");
    for (i = 0; i <= l; i++) {
        sprintf(buf, "%ld", i+1);
        if (i == l)
            strcat(buf, "+");
        fprintf(fp,"\t\tronde  %-10s = %s;\n", buf,
                fpen(country_penalty[i]));
    }
    fprintf(fp, "\t\tronde  12+        =  0; # Mettre INFINI pour le championnat du monde.\n"
            "                                        # INFINI should be used for the WOC.\n");

#ifdef ELITISM
    /*
     * Elitisme - elitism
     */
    for (l = i = NMAX_ROUNDS-1; l >= 0; l--)
        if (elitism_penalty[l] != elitism_penalty[i])
        { ++l; break; }

            fprintf(fp,"\n\tElitisme:\n");
    for (i = 0; i <= l; i++) {
        sprintf(buf, "%ld", i+1);
        if (i == l)
            strcat(buf, "+");
        fprintf(fp,"\t\tronde  %-10s = %s;\n", buf,
                fpen(elitism_penalty[i]));
    }
#endif

    fprintf(fp,"};\n");
}

/* Initialisation de l'alloc-ring - alloc-ring initialization */
static long allo_ring_initialise = 0;
long  alloc_ring_index = 0;
char *alloc_ring[ALLOC_RING_SLOTS];

void init_alloc_ring (void) {
    long i;

    if (!allo_ring_initialise) {
        for (i = 0; i < ALLOC_RING_SLOTS; i++)
            CALLOC(alloc_ring[i], ALLOC_RING_LENGTH, char);
        allo_ring_initialise = 1;
    }
}

char *new_string (void) {
    /* faut-il initialise l'alloc-ring ? - alloc-ring to be init ? */
    if (!allo_ring_initialise)
        init_alloc_ring();
    /*
     * renvoyer la premiere chaine vierge,
     * ou la plus ancienne si elles sont toutes prises
     ****
     * returns first empty string or most ancient one if all are taken
     */
    if (++alloc_ring_index >= ALLOC_RING_SLOTS)
        alloc_ring_index = 0;
    return alloc_ring[alloc_ring_index];
}

/*
 * trivialloc: allocation triviale de memoire, sans alignement particulier,
 * et cette memoire ne pourra jamais etre liberee! Tres utile cependant
 * pour allouer un grand nombre de petites chaines de caracteres.
 ****
 * trivialloc: trivial memory allocation, without alignment and this
 * memory will never be freed! Very useful to allocate a big number of
 * small strings.
 */

#ifdef __BORLANDC__
  #define MINIMAL_BLOCK_SIZE 8192
  #include <malloc.h>
#else
  #define MINIMAL_BLOCK_SIZE 8192
#endif

char *trivialloc (long length) {
    static char *block;
    char *allo;
    static long free_length = 0;

    assert(length > 0);
    if (free_length < length)
        CALLOC(block, free_length = MINIMAL_BLOCK_SIZE, char);
    allo = block;
    block  += length;
    free_length -= length;
    assert(free_length >= 0);
    return allo;
}

/* Fonctions pour copier deux chaines de caracteres
 * et concatener deux chaines dans une troisieme.
 ****
 * Functions to copy two strings and concatenate two strings into a third.
 */

void COPY(const char *source, char **dest) {
    char *tmp ;

    assert((dest != NULL) && (source != NULL)) ;

    tmp = trivialloc(strlen(source)+1) ;
    strcpy(tmp, source) ;
    *dest = tmp ;
}

void CONCAT(const char *source, const char *added, char **dest) {
    char * tmp ;

    assert((dest != NULL) && ((source != NULL) || (added != NULL))) ;

    if (source == NULL) {
        COPY(added, dest) ;
    } else if (added == NULL) {
        COPY(source, dest) ;
    } else {
        tmp=trivialloc(strlen(source)+strlen(added)+1);
        strcpy(tmp,source) ;
        strcat(tmp,added) ;
        *dest = tmp ;
    }
}

/*
 * max_of et min_of : fonctions retournant le max et le min de deux entiers longs.
 * Il vaut mieux les nommer ainsi pour eviter des problemes dans les environnements
 * definissant leurs propres fonctions max et min.
 ****
 * max_of et min_of: functions returning max and min of two long integers.
 * they have been named that way to prevent collision with environement
 * defining their own max and min functions.
 *
 */

long max_of(long n1, long n2) {
    return ((n1 > n2) ? n1 : n2);
}

long min_of(long n1, long n2) {
    return ((n1 > n2) ? n2 : n1);
}


/*
 * Une petite fonction pour generer des noms de fichiers qui se suivent.
 * Si pattern contient la chaine "###", alors ### est remplace par ID;
 * sinon, on ajoute seulement ID a la fin de pattern.
 * Ex : pattern == "class###.txt" et ID == 11   -> on renvoie class_11.txt
 *      pattern == "classement" et ID == 11   -> on renvoie classement_11
 * Cette fonction alloue de la memoire pour la chaine resultat, qui ne sera jamais
 * liberee.
 *****
 * Small function to generate successive filenames. If pattern contains string '###',
 * then ### is replaced by ID; otherwise ID is added and the end of pattern.
 * Ex : pattern == "stand###.txt" and ID == 11  -> returns stand_11.txt
 *      pattern == "standings" and ID == 11     -> returns standings_11 
 * This function allocates memory for the result string which will never be freed.
 */
char *numbered_filename(char *pattern, long number) {
    char *name, *hashtag_occur;
    long len;

    assert( (number >= 0) && (number <= 999));

    len = strlen(pattern);
    name = trivialloc( len + 4 );
    strcpy(name, pattern);
    name[len]   = '\0';
    name[len+1] = '\0';
    name[len+2] = '\0';
    name[len+3] = '\0';
    hashtag_occur = strstr(name, "###");

    if (hashtag_occur == NULL)
        hashtag_occur = name + len;

    hashtag_occur[0] = ((number < 100)? '_' : '0' + ((number/100) % 10));
    hashtag_occur[1] = ((number < 10) ? '_' : '0' + ((number/10) % 10));
    hashtag_occur[2] = '0' + ((number) % 10);

    return name;
}

/* long different_beginnings(s1,s2,n):
 *  Renvoie 0 si
 *    - la longueur de s1 est plus petite que celle de s2, et
 *    - les n premiers caracteres des 2 chaines coincident;
 *  Renvoie 1 sinon.
 *  Pas de distinction majuscules/minuscules.
 ****
 * long different_beginnings(s1,s2,n):
 *  Returns 0 if
 *   - length of s1 is smaller than length of s2 and
 *   - n first characters of both strings are equal.
 *  Return 1 otherwise
 * No case distinction
 */
long different_beginnings(const char *s1, const char *s2, long n) {
    long len1,len2,i;
    char c1,c2;

    assert((s1 != NULL) && (s2 != NULL));
    len1 = strlen(s1);
    len2 = strlen(s2);

    if (len1 > len2) return 1;

    if (n>len1) n=len1;
    if (n>len2) n=len2;

    for (i=0;i<n;i++)
    {
        c1=toupper(s1[i]);
        c2=toupper(s2[i]);
        if (c1 != c2) return 1;
    }
    return 0;
}

 /*
  * int compare_strings_insensitive(const char *scan1, const char *scan2);
  * <0 for <, 0 for ==, >0 for >
  */
long compare_strings_insensitive(const char *scan1, const char *scan2) {
    register char c1, c2;

    if (!scan1)
        return scan2 ? -1 : 0;
    if (!scan2) return 1;

    do {
        c1 = *scan1++; c1=toupper(c1);
        c2 = *scan2++; c2=toupper(c2);
    } while (c1 && c1 == c2);

    /*
     * The following case analysis is necessary so that characters
     * which look negative collate low against normal characters but
     * high against the end-of-string NUL.
     */
    if (c1 == c2)
        return(0);
    else if (c1 == '\0')
        return(-1);
    else if (c2 == '\0')
        return(1);
    else
        return(c1 - c2);
}


/* Recopie un fichier dans un autre. Les fichiers sont deja ouverts. 
 * renvoie -1 si erreur. Convertit les fins de ligne 
 ****
 * Copy a file into another one. Files should already be opened.
 * return -1 if error. Converts end of line.
 */
long copy_files(FILE *dest, FILE *source) {
    long c, cc ;
    unsigned long i, end_of_line ;
    char str_line[256] ;

    if (dest == NULL || source == NULL)
        return -1 ;
    do {
        i=0 ; end_of_line=0 ; /* on commence une nouvelle ligne - start of a new line */
        do {
            c = fgetc(source) ;
            if (c != EOF && c != 0x0D && c != 0x0A && i<(sizeof(str_line)-1))
                str_line[i++] = c ;
            else {
                end_of_line = 1 ;
                if (c == 0x0D) {
                    cc = fgetc(source) ;
                    if (cc != 0x0A)
                        ungetc(cc, source) ;
                }
            }
        } while (!end_of_line) ;
        str_line[i] = '\0' ;
        if (fprintf(dest,"%s\n", str_line) <0)
            return -1 ;
    } while (c != EOF) ;
    return 0 ;
}

/* Effectue une sauvegarde du fichier intermediaire dans le sous-dossier si on l'utilise
 ****
 * Save workfile into subfolder (if used)
 */
void backup_workfile(void) {
    FILE *papp_file ;
    FILE *papp_backup_file ;
    if (use_subfolder != 1)
        return ;
    if ((papp_file = myfopen_in_subfolder(workfile_filename, "rb", "", 0, 0)) == NULL)
        return ;
    if ((papp_backup_file = myfopen_in_subfolder(backup_workfile_filename, "wb", subfolder_name, use_subfolder, 0)) == NULL) {
        fclose(papp_file) ;
        return ;
    }
    copy_files(papp_backup_file, papp_file) ;
    fclose(papp_file) ;
    fclose(papp_backup_file) ;
}

/* Sauvegarde les infos du tournoi dans le fichier intermediaire EN ECRASANT CE QU'IL Y A !!
 ****
 * Saves tournament infos into workfile ERASING ALL THERE IS!!
 */
void save_tournament_infos(void) {
    FILE *papp_file ;

    papp_file = myfopen_in_subfolder(workfile_filename, "wb", "", 0, 1) ;
    /* Sauvegarde des informations dans "papp-internal-workfile.txt"
     * Attention, les mots-clefs doivent etre les memes que dans l'analyse lexicale (pap.l).
     ****
     * Save infos in "papp-internal-workfile.txt"
     * Beware keywords should be the same as for lexical analysis (pap.l)
     */
    fprintf(papp_file, "#########################################\n") ;
    fprintf(papp_file, "# %s, %s\n", VERSION, __DATE__) ;
    fprintf(papp_file, "#########################################\n") ;
    fprintf(papp_file, "#_Nom-Tournoi   = \"%s\" ;\n", tournament_name) ;
    fprintf(papp_file, "#_Nombre-Rondes = %ld ;\n", number_of_rounds) ;
    fprintf(papp_file, "#_BQ-double = %f ;\n", brightwell_coeff*2) ;
    fprintf(papp_file, "#_Date = \"%s\" ;\n", tournament_date) ;
    fprintf(papp_file, "#########################################\n\n") ;
    fclose(papp_file) ;
}


/* Initialise un fichier intermediaire avec les infos du tournoi.
 * Si file_type = OLD, on recopie a la suite l'ancien fichier
 ****
 * Initialize a workfile with tournament infos. If file_type = OLD, the
 * old file is copy and added ant the end.
 */
void init_workfile(long file_type) {
    char tmp_filename[] = "temp.XXXXXX" ;
    FILE *tmpfile ;
    FILE *papp_file ;
    long err ;

    if (file_type == OLD) {
        /* On sauvegarde l'ancien "papp-internal-workfile.txt" - old workfile is saved */
        if (mkstemp(tmp_filename) == -1)
        	return ;
        papp_file = myfopen_in_subfolder(workfile_filename, "rb", "", 0, 1) ;
        if ((tmpfile = myfopen_in_subfolder(tmp_filename, "wb", "", 0, 0)) == NULL) {
            fclose(papp_file) ;
            return ;
        }
        err = copy_files(tmpfile, papp_file) ;
        fclose(papp_file) ;
        fclose(tmpfile) ;
        if (err == -1)
            return ;

    }
    save_tournament_infos() ; /* recree un fichier intermediaire - workfile is recreated */
    if (file_type == OLD) {
        papp_file = myfopen_in_subfolder(workfile_filename, "ab", "", 0, 1) ;
        /* On recopie l'ancien "papp-internal-workfile.txt" - old workfile is copied */
        if ((tmpfile = myfopen_in_subfolder(tmp_filename, "rb", "", 0, 0)) != NULL) {
            copy_files(papp_file, tmpfile) ;
            fclose(tmpfile) ;
            remove(tmp_filename) ;
        }
        fclose(papp_file) ;

    }
}

/* fopen() ne sait pas ouvrir un fichier se trouvant dans un sous-dossier ("dir/truc.txt")
 * si le sous-dossier n'existe pas. Il faut d'abord le creer puis ouvrir le fichier
 * Cette fonction verifie
 * 1. qu'on utilise le sous-dossier (variable 'used' vaut 1) ;
 * 2. que le sous-dossier de fullname indique existe ou peut etre creer
 * puis ouvre le fichier indique avec son mode d'ouverture
 * -> Affiche une erreur fatale si le fichier ne s'ouvre pas et que le param 'err' est a 1
 ****
 * fopen() doesn't know how to open a file into a subfolder ("dir/truc.txt") if the subfolder
 * doesn't exist. It must first be created then the file opened. This function checks that:
 * 1. subfolder is used ('used' variable is set to 1),
 * 2. that subfolder in fullname exists or can be created.
 * then opens the file with the given opening mode.
 * -> Displays a fatal error if the file doesn't open and 'err' parameter is set to 1.
 */

FILE *myfopen_in_subfolder(const char *filename, const char *mode, const char *folder, long used, long err) {
    FILE *fp ;
    char line[512] ;

    assert(filename && folder) ;
    assert(filename[0] != '\0') ;
    if (used == 1) {
        /* On essaie de creer le sous-dossier ; s'il existe, tant mieux !
         ****
         * We try to create subfolder; if it exists, that's good! */
        mkdir(folder, 0777) ;
    }
    fp = fopen(filename, mode) ;
	if ((fp == NULL) && (err == 1)) {
        sprintf(line, "%s " FATAL_OPEN "%s", filename, strerror(errno)) ;
        fatal_error(line);
    }
    return fp ;
}
