%{
/*
 * pap.y: Analyse syntaxique du fichier de configuration
 * ainsi que des fichiers de joueurs.
 *
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 25/06/2012 : v1.34, Ajout du mot-clef 'KW_XML' indiquant s'il faut generer
                     les fichiers XML de sortie.
 * (EL) 20/07/2008 : v1.33, Ajout du token 'DIESE_DATE' pour lire la ligne du fichier intermediaire.
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 30/03/2007 : Ajout des mots-clefs 'KW_DOSSIER', 'KW_TRUE' et 'KW_FALSE' indiquant
 *                   s'il faut regrouper tous les fichiers class/ronde/result...
 *                   dans un meme sous-dossier portant le nom du tournoi.
 * (EL) 07/02/2007 : Ajout des tokens 'DIESE_NOM', 'DIESE_RONDES' et
 *                   'DIESE_BRIGHTWELL_DBL' pour lire les lignes correspondantes
 *                   dans le fichier intermediaire.
 *                   Ajout des regles dans les commandes internes.
 * (EL) 04/02/2007 : Ajout du token 'KW_BRIGHTWELL_DBL' qui donne
 *                   la constante lorsqu'on compte 1 point par victoire.
 * (EL) 04/02/2007 : Test de la presence du type precedent (<= 1.29)
 *                   du fichier de config (avec Brightwell ou toutes-rondes)
 *                   -> memoriser dans variable 'config_file_type'
 * (EL) 02/02/2007 : Ajout du token 'INT_ET_DEMI' pour distinguer
 *                   du token 'DOUBLE' utilise uniquement pour
 *                   definir la constante de Brightwell.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * pap.y: Syntaxical analysis of configuration file and players file.
 *
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 25/06/2012 : v1.34, Add 'XML' keyword for XML file generation
 * (EL) 20/07/2008 : v1.33, v1.33, add recognition of 'DIESE_DATE' token in workfile.
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 30/03/2007 : Add 'KW_DOSSIER', 'KW_TRUE' and 'KW_FALSE' tokens indicating
 *                   grouping of class/result/pairings files... in the same sub-directory
 *                   having tournament name as filename
 * (EL) 07/02/2007 : Add 'DIESE_NOM', 'DIESE_RONDES' and 'DIESE_BRIGHTWELL_DBL' tokens
 *                   to be able to put these infos in workfile
 *                   Addition of rules in internal commands.
 * (EL) 04/02/2007 : Add 'KW_BRIGHTWELL_DBL' token which gives BQ constant when 1 point is used for a victory
 * (EL) 04/02/2007 : Testing presence of previous version (<= 1.29) in configuration file.
 *                   -> memorisation in 'config_file_type'
 * (EL) 02/02/2007 : Add 'INT_ET_DEMI' token to distinguish from token 'DOUBLE' used only
 *                   to define Brightwell constant.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 */


#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "pairings.h"
#include "couplage.h"
#include "global.h"
#include "player.h"
#include "discs.h"

#define yyerror(x)

#if defined(__MSDOS__) && !defined(MSDOS)
# define MSDOS
#endif

#if defined(__THINK_C__) || defined(PAPP_MAC_METROWERKS)
# define alloca malloc
#endif

#ifdef __BORLANDC__
#include <malloc.h>
#endif

#ifdef ENGLISH
# define NUM_ZERO               "player %s has number 0"
# define FOUND_DUP              "player %ld already exists"
# define BIP_ERROR              "impossible Bye score"
# define BIP_WINS               "Bye shouldn't win games"
# define BIP_TOTAL              "the total number of discs must be >0"
# define TTR_BADP               "bad parameters for toutes-rondes"
# define ZIS_BADP               "bad parameters for zone-insertion"
# define PEN_NEGATIVE           "negative penalty, raised to zero"
# define PEN_INFINITE           "huge penalty truncated"
# define PENC_NULL              "null color difference, penalty ignored"
# define PENF_NULL              "null float, penalty ignored"
# define PENCH_NULL             "null round, penalty ignored"
# define PENEL_NULL             "null round, penalty ignored"
# define PENCH_TOOFAR           "round number too great, penalty ignored"
# define PENEL_TOOFAR           "round number too great, penalty ignored"
# define ICMD_BPAIR             "bad pairing"
# define ICMD_APAIR             "player(s) already paired"
# define ICMD_TOTS              "bad total of discs"
# define ICMD_BRES              "bad result"
# define ICMD_DEMI              "impossible half-disc result"
# define TTR_TOOSMALL           "table-toutes-rondes is too small"
# define TTR_TOOBIG             "table-toutes-rondes is too big"
# define PLR_UNKNOWN            "unknown player %ld"
# define CMD_UNKNOWN            "unknown command name"
# define ERR_SYNTAX             "syntax error"
# define M_SEMICOLON            "missing semicolon"
# define ICMD_BAD_RND           "Bad number of rounds in 'papp-workfile' file"
# define ICMD_BAD_BC            "Bad Brightwell constant in 'papp-workfile' file"
#else
# define NUM_ZERO               "Le joueur %s a le numero 0"
# define FOUND_DUP              "le joueur %ld existe deja"
# define BIP_ERROR              "le score de Bip est incorrect"
# define BIP_WINS               "Bip ne devrait pas gagner"
# define BIP_TOTAL              "le total de pions doit etre >0"
# define TTR_BADP               "mauvais parametres pour toutes-rondes"
# define ZIS_BADP               "mauvais parametres pour zone-insertion"
# define PEN_NEGATIVE           "penalite negative, ramenee a zero"
# define PEN_INFINITE           "penalite trop grande, tronquee"
# define PENC_NULL              "ecart chromatique nul, penalite ignoree"
# define PENF_NULL              "flottement nul, penalite ignoree"
# define PENCH_NULL             "ronde zero, penalite ignoree"
# define PENEL_NULL             "ronde zero, penalite ignoree"
# define PENCH_TOOFAR           "ronde trop eloignee, penalite ignoree"
# define PENEL_TOOFAR           "ronde trop eloignee, penalite ignoree"
# define ICMD_BPAIR             "mauvais appariement"
# define ICMD_APAIR             "joueur(s) deja doPairings(s)"
# define ICMD_TOTS              "mauvais total de pions"
# define ICMD_BRES              "mauvais resultat"
# define ICMD_DEMI              "resulat en demi pions impossible"
# define TTR_TOOSMALL           "table-toutes-rondes trop petite"
# define TTR_TOOBIG             "table-toutes-rondes trop grande"
# define PLR_UNKNOWN            "joueur %ld inconnu"
# define CMD_UNKNOWN            "nom de commande inconnu"
# define ERR_SYNTAX             "erreur de syntaxe"
# define M_SEMICOLON            "point-virgule attendu"
# define ICMD_BAD_RND           "Mauvais nombre de rondes dans le fichier intermediaire"
# define ICMD_BAD_BC            "Mauvaise constante de Brightwell dans le fichier intermediaire"
#endif

static char buffer[80];
static long i, taille_ttr, *table_ttr;
int yylex (void);

static char *end_to_end(char *s1, char *s2) {
    char *s3, *p, *q;

    s3 = new_string();
    p = s1; q = s3;
    while ((*q++ = *p++))
        ;
    q[-1] = ' ';
    p = s2;
    while ((*q++ = *p++))
        ;
    return s3;
}

static void New_player(long number, const char *name, const char *firstname,
        const char *programmer, const char *country, long rating,
        const char *comment, const long new) {
    if (!number) {
        char *s = new_string();
        sprintf(s, NUM_ZERO, name);  /* 0 number forbidden */
        syntax_error(s);
    }
    if (new_player(number, name, firstname, programmer, country, rating, comment, new) == NULL) {
        char *s = new_string();
        sprintf(s, FOUND_DUP, number);  /* duplicate */
        syntax_error(s);
    }
}

static void enlarge_table (pen_t **table, long *size, long new_size) {
    long i, old_size;

    old_size = *size;
    assert(0 < old_size && old_size < new_size);
    REALLOC(*table, new_size, pen_t);
    for (i = old_size; i < new_size; i++) {
        (*table)[i] = (*table)[old_size-1];
    }
    *size = new_size;
}

static void clear_all_penalties (void) {
    long i;

    /* Colour penalties */
    for (i = 0; i < colors_nmax; i++)
        color_penalty[i] = 0;
    repeated_color_penalty = 0;

    /* Flotting penalties */
    for (i = 0; i < floats_nmax; i++)
        float_penalty[i] = 0;
    cumulative_floats_penalty = opposite_float_pen = 0;

    /* Repetition penalties */
    same_colors_replay_penalty   = opposite_colors_replay_penalty   =
    immediate_replay_penalty = bye_replay_penalty = 0;

    /* Same country penalties */
    for (i = 0; i < NMAX_ROUNDS; i++)
        country_penalty[i] = 0;

    /* Elitisme penalties */
#ifdef ELITISM
    for (i = 0; i < NMAX_ROUNDS; i++)
        elitism_penalty[i] = 0;
#endif
}

%}

%union {
        long integer;
        char *string;
        double reel;
        }

%token TOK_NEWLINE KW_RAZ_COUPL KW_SCORE_BIP
%token KW_COPP KW_TTRONDES KW_TABTTR INVALID_KEYWORD
%token KW_SAUVEGARDE KW_SAUVIMM KW_SAUVDIF KW_BIPBIP
%token KW_IMPRESSION KW_MANUELLE KW_AUTOMATIQUE
%token KW_MINORATION KW_AFF_PIONS KW_AFF_NORMAL KW_AFF_DIFF
%token KW_FICHIER KW_JOUEURS KW_NOUVEAUX KW_INTER KW_LOG
%token KW_RESULT KW_CLASS KW_EQUIPES KW_APPARIEMENTS
%token KW_CROSSTABLE KW_ELO
%token KW_PENALITES KW_PAYS  KW_ZONE_INS
%token KW_FOIS KW_DPOINT KW_COULEUR  KW_FLOTTEMENT
%token KW_REPET KW_MCOUL KW_DESUITE KW_RONDESUIV
%token KW_CHAUVIN KW_RONDE KW_ELITISME
%token KW_BRIGHTWELL KW_BRIGHTWELL_DBL
%token KW_DOSSIER KW_XML KW_TRUE KW_FALSE

%token DIESE_NOM DIESE_RONDES DIESE_BRIGHTWELL_DBL DIESE_DATE

%token <string>  STRING COMMENT
%token <integer> INTEGER
%token <reel> DOUBLE
%token <reel> INT_ET_DEMI

%type  <integer> entier_signe opt_jech opt_plus v_pen
%type  <string>  chaines opt_chaines opt_pays opt2_pays opt_comm
%type  <reel>  nbr_reel

%%

entree: /* nothing */
        | entree_config
        | entree_joueurs
        ;

entree_joueurs:    _entree_joueurs
        | newlines _entree_joueurs
        ;

_entree_joueurs:          ligne_joueur newlines
        | _entree_joueurs ligne_joueur newlines
        ;

newlines: TOK_NEWLINE
        | newlines TOK_NEWLINE;

ligne_joueur:
          indication_pays
        | description_joueur
        | ligne_invalide
        ;

indication_pays: KW_PAYS '=' STRING
            { COPY($3, &current_country); }
        ;

description_joueur:
          INTEGER chaines STRING opt_pays opt_jech opt_comm
                { New_player($1,$2,$3,NULL,$4,$5,$6, new_players_file); }
        | INTEGER chaines ',' opt_chaines opt_pays opt_jech opt_comm
                { New_player($1,$2,$4,NULL,$5,$6,$7, new_players_file); }
        | INTEGER STRING opt_pays opt_jech opt_comm
                { New_player($1,$2,NULL,NULL,$3,$4,$5, new_players_file); }
        | INTEGER chaines '(' opt_chaines ')' opt_pays opt_jech opt_comm
                { New_player($1,$2,NULL,$4,$6,$7,$8, new_players_file); }
        | INTEGER '{' STRING '}' opt_comm
                { change_nationality($1,$3); }
        ;

chaines:  STRING
        | chaines STRING        { $$ = end_to_end($1, $2);  }
        | chaines '+'           { $$ = end_to_end($1, "+"); }
        | chaines '/'           { $$ = end_to_end($1, "/"); }
        ;

opt_chaines: /* nothing */      { $$ = ""; }
        | chaines               { $$ = $1; }
        ;

opt_jech: /* nothing */         { $$ =  0; }
        | '<' entier_signe '>'  { $$ = $2; }
        ;

entier_signe: INTEGER           { $$ =  $1; }
        | '-' INTEGER           { $$ = -$2; }
        ;

opt_pays: /* nothing */         { $$ = current_country; }
        | '{' STRING '}'        { $$ = $2; }
        ;

opt2_pays: /* nothing */        { $$ = ""; }
        | STRING                { $$ = $1; }
        ;

opt_comm: /* nothing */         { $$ = NULL; }
        | COMMENT               { $$ = $1;   }
        ;

nbr_reel: INTEGER               { $$ = (double) $1; }
        | INT_ET_DEMI           { $$ = $1 ; }
        | DOUBLE                { $$ = $1 ; }
        ;

entree_config:
          commande point_virgule
        | entree_config commande point_virgule
        ;

commande: /* nothing */
        | KW_XML KW_TRUE
                { generate_xml_files = 1;}
        | KW_XML KW_FALSE
                { generate_xml_files = 0;}
        | KW_DOSSIER KW_TRUE
                { use_subfolder = 1 ;}
        | KW_DOSSIER KW_FALSE
                { use_subfolder = 0 ;}
        | KW_FICHIER KW_JOUEURS  '=' STRING
                { COPY($4, &players_filename); }
        | KW_FICHIER KW_NOUVEAUX '=' STRING
                { COPY($4, &new_players_filename); }
        | KW_FICHIER KW_INTER    '=' STRING
                { COPY($4, &workfile_filename); }
        | KW_FICHIER KW_RESULT   '=' STRING
            { COPY($4, &results_filename);
            result_file_save = results_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_CLASS    '=' STRING
            { COPY($4, &standings_filename);
            standings_file_save = standings_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_EQUIPES  '=' STRING
            { COPY($4, &team_standings_filename);
            team_standings_file_save = team_standings_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_APPARIEMENTS   '=' STRING
            { COPY($4, &pairings_filename);
            pairings_file_save = pairings_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_CROSSTABLE   '=' STRING
            { COPY($4, &HTML_crosstable_filename);
            html_crosstable_file_save = HTML_crosstable_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_ELO   '=' STRING
            { COPY($4, &elo_filename);
            elo_file_save = elo_filename[0]? 1 : 0 ; }
        | KW_FICHIER KW_LOG      '=' STRING
                { COPY($4, &log_filename); }
        | KW_ZONE_INS opt2_pays  '=' INTEGER '-' INTEGER
            {
                /*
                 * zone-insertion [pays] = inf-sup;
                 */
                if ($4<0 || $4>$6) {
                    /* bad parametres */
                    syntax_error(ZIS_BADP);
                } else
                    newInsertionZone($2,$4,$6);
            }
        | indication_pays
        | KW_BRIGHTWELL '=' nbr_reel
            {
                brightwell_coeff = $3;
                config_file_type = OLD ; /* Old config file */
            }
        | KW_BRIGHTWELL_DBL '=' nbr_reel
            {
                brightwell_coeff = ($3)/2;
                config_file_type = OLD ; /* Old config file */
            }
        | KW_SCORE_BIP  '=' INTEGER
            {
                bye_score = INTEGER_TO_SCORE($3);
                if (SCORE_TOO_LARGE(bye_score))
                    syntax_error(BIP_ERROR);
                if (IS_VICTORY(bye_score))
                    syntax_warning(BIP_WINS);
            }
        | KW_SCORE_BIP  '=' INTEGER '/' INTEGER
            {
                bye_score = INTEGER_TO_SCORE($3);
                discsTotal = $5;
                if (discsTotal < 1)
                    syntax_error(BIP_TOTAL);
                else
                    scores_digits_number = number_of_digits(discsTotal);

                if (SCORE_TOO_LARGE(bye_score))
                    syntax_error(BIP_ERROR);
                else if (IS_VICTORY(bye_score))
                    syntax_warning(BIP_WINS);
            }
        | KW_COULEUR '=' '{' STRING ',' STRING '}'
            { COPY($4, &color_1); COPY($6, &color_2); }
        | KW_SAUVEGARDE KW_SAUVIMM
                { immediate_save = 1; }
        | KW_SAUVEGARDE KW_SAUVDIF
                { immediate_save = 0; }
        | KW_IMPRESSION KW_MANUELLE
            { automatic_printing = 0; }
        | KW_IMPRESSION KW_AUTOMATIQUE INTEGER
            { print_copies = $3;
            automatic_printing = print_copies? 1 : 0 ; }
        | KW_IMPRESSION KW_AUTOMATIQUE
            { print_copies = 1;
            automatic_printing = 1; }
        | KW_AFF_PIONS KW_AFF_DIFF
                { display_score_diff = 1; }
        | KW_AFF_PIONS KW_AFF_NORMAL
                { display_score_diff = 0; }
        | KW_TTRONDES '=' INTEGER '-' INTEGER KW_JOUEURS
            {
                /*
                 * round-robin = inf-sup players
                 */
                config_file_type = OLD ; /* Old config file */
                if ($3 < 1 || $3 > $5) {
                    /* bad parametres */
                    syntax_error(TTR_BADP);
                } else {
                    minPlayers4rr = $3;
                    maxPlayers4rr = $5;
                }
            }
        | KW_PENALITES '{'
            {
                /*
                 * Clear all penalties before starting to read different sections
                 */

                clear_all_penalties();
            }
        liste_sections '}'
        | commande_interne
        | ligne_invalide
        ;

liste_sections: /* nothing */
        | liste_sections section
        ;

section:  section_couleur
        | section_flottement
        | section_repetition
        | section_chauvinisme
        | section_elitisme
        ;

section_couleur:     KW_COULEUR    ':' liste_pen_couleur;
section_flottement:  KW_FLOTTEMENT ':' liste_pen_flottement;
section_repetition:  KW_REPET      ':' liste_pen_repetition;
section_chauvinisme: KW_CHAUVIN    ':' liste_pen_chauvinisme;
section_elitisme:    KW_ELITISME   ':' liste_pen_elitisme;

liste_pen_couleur: /* nothing */
        | liste_pen_couleur pen_couleur point_virgule
        ;

liste_pen_flottement: /* nothing */
        | liste_pen_flottement pen_flottement point_virgule
        ;

liste_pen_repetition: /* nothing */
        | liste_pen_repetition pen_repetition point_virgule
        ;

liste_pen_chauvinisme: /* nothing */
        | liste_pen_chauvinisme pen_chauvinisme point_virgule
        ;

liste_pen_elitisme: /*nothing */
    | liste_pen_elitisme pen_elitisme point_virgule
    ;

opt_plus: /* nothing */ { $$ = 0; }
        | '+'           { $$ = 1; }
        ;

v_pen: INTEGER
        {
            /*
             * Returns an integer between 0 and MAX_PEN
             */
            long value = $1;
            if (value < 0) {
                syntax_warning(PEN_NEGATIVE);
                value = 0;
            } else if (value > MAX_PEN) {
                /* LONG_MAX means infinitiy */
                if (value != LONG_MAX) {
                    syntax_warning(PEN_INFINITE);
                }
                value = MAX_PEN;
            }
            $$ = value;
        }
        ;

pen_couleur: /* nothing */
        | INTEGER opt_plus KW_FOIS '=' v_pen
            {
                i = $1;
                if (i < 1) {
                    syntax_warning(PENC_NULL);
                } else {
                    if (i+1 >= colors_nmax) {
                        enlarge_table(&color_penalty,
                                &colors_nmax,i+2);
                    }
                    while (i < colors_nmax) {
                                color_penalty[i++] = $5;
                                if ($2==0) break;
                    }
                }
            }
        | KW_DESUITE '=' v_pen
            { repeated_color_penalty = $3; }
        ;

pen_flottement: /* nothing */
        | INTEGER opt_plus KW_DPOINT '=' v_pen
            {
                i = $1;
                if (i < 1) {
                    syntax_warning(PENF_NULL);
                } else {
                    if (i+1 >= floats_nmax) {
                        enlarge_table(&float_penalty,
                                &floats_nmax, i+2);
                    }
                    while (i < floats_nmax) {
                        float_penalty[i++] = $5;
                        if ($2==0) break;
                    }
                }
            }
        | KW_MINORATION '=' v_pen
                { opposite_float_pen = $3; }
        | KW_DESUITE '=' v_pen
                { cumulative_floats_penalty = $3; }
        ;

pen_repetition: /* nothing */
        | KW_MCOUL '=' v_pen
                { same_colors_replay_penalty = $3; }
        | KW_COPP '=' v_pen
                { opposite_colors_replay_penalty = $3; }
        | KW_BIPBIP '=' v_pen
                { bye_replay_penalty = $3; }
        | KW_DESUITE '=' v_pen
                { immediate_replay_penalty = $3; }
        ;

pen_chauvinisme: /* nothing */
        | KW_RONDE INTEGER opt_plus '=' v_pen
            {
                i = $2;
                if (i < 1)
                        syntax_warning(PENCH_NULL);
                else if (i > NMAX_ROUNDS)
                        syntax_warning(PENCH_TOOFAR);
                else while (i <= NMAX_ROUNDS) {
                        country_penalty[i-1] = $5;
                        i++;
                        if ($3 == 0) break;
                    }
            }
        ;

pen_elitisme: /* nothing */
    | KW_RONDE INTEGER opt_plus '=' v_pen
       {
#ifdef ELITISM

                i = $2;
                if (i < 1)
                        syntax_warning(PENEL_NULL);
                else if (i > NMAX_ROUNDS)
                        syntax_warning(PENEL_TOOFAR);
                else while (i <= NMAX_ROUNDS) {
                        elitism_penalty[i-1] = $5;
                        i++;
                        if ($3 == 0) break;
                    }

#else
        i = $2;
        if (i > 0) {
            puts("WARNING : Elitism penalty ignored in the version of Papp!");
            /* read_key(); */
        }

#endif
       }
       ;


commande_interne:
		  DIESE_DATE '=' STRING
			{   COPY($3, &tournament_date); }

		| DIESE_NOM '=' STRING
            {
                COPY($3, &tournament_name);
                workfile_type = CORRECT ;
            }
        | DIESE_RONDES '=' INTEGER
            {
                if( ($3<1) || ($3>NMAX_ROUNDS) )
                    syntax_error(ICMD_BAD_RND) ;
                else {
                    number_of_rounds = $3 ;
                    /* If 2n-1 or 2n rounds, there are 2n max players for a round-robin */
                    maxPlayers4rr = ((number_of_rounds+1)/2)*2 ;
                    minPlayers4rr = 1 ;
                }
            }
        | DIESE_BRIGHTWELL_DBL '=' nbr_reel
            { brightwell_coeff = ($3)/2; }
        | KW_RONDESUIV
            {
                tables_numbering();
                updateScores();
                next_round();
            }
        | '&' liste_inscrits
            {
                absents_uncoupling();
            }
        | '(' INTEGER INTEGER ')'
            {
                long Jn=$2, Jb=$3, n1, n2;
                n1 = inscription_ID(Jn);
                n2 = inscription_ID(Jb);
                if (n1<0 || n2<0 || !present[n1] || !present[n2])
                        /* Bad pairing */
                        syntax_error(ICMD_BPAIR);
                else if (polarity(Jn) || polarity(Jb))
                        /* deja apparies */
                        syntax_error(ICMD_APAIR);
                else
                        make_couple(Jn, Jb, UNKNOWN_SCORE);
            }
        | '(' INTEGER INTEGER INTEGER ')'
            {
                long Jn=$2, Jb=$3, n1, n2;
                n1 = inscription_ID(Jn);
                n2 = inscription_ID(Jb);
                if (n1<0 || n2<0 || !present[n1] || !present[n2])
                        syntax_error(ICMD_BPAIR);
                else if (polarity(Jn) || polarity(Jb))
                        syntax_error(ICMD_APAIR);
                else
                        make_couple(Jn, Jb, INTEGER_TO_SCORE($4));
            }
        | '(' INTEGER INTEGER INT_ET_DEMI ')'
            {
#ifndef USE_HALF_DISCS
            beep();
            syntax_error(ICMD_DEMI);
#else
                long Jn=$2, Jb=$3, n1, n2;
                double Scn=$4;
                n1 = inscription_ID(Jn);
                n2 = inscription_ID(Jb);
                if (n1<0 || n2<0 || !present[n1] || !present[n2])
                        syntax_error(ICMD_BPAIR);
                else if (polarity(Jn) || polarity(Jb))
                        syntax_error(ICMD_APAIR);
                else
                        make_couple(Jn, Jb, FLOAT_TO_SCORE(Scn));
#endif
            }
        | '(' INTEGER INTEGER INTEGER INTEGER ')'
            {
                long Jn=$2, Scn=$3, Jb=$4, Scb=$5, n1, n2;
                if (BAD_TOTAL(INTEGER_TO_SCORE(Scn),INTEGER_TO_SCORE(Scb)))
                        syntax_error(ICMD_TOTS);
                n1 = inscription_ID(Jn);
                n2 = inscription_ID(Jb);
                if (n1<0 || n2<0 || !present[n1] || !present[n2])
                        syntax_error(ICMD_BRES);
                else if (polarity(Jn) || polarity(Jb))
                        syntax_error(ICMD_APAIR);
                else
                        make_couple(Jn, Jb, INTEGER_TO_SCORE(Scn));
            }
        | '(' INTEGER INT_ET_DEMI INTEGER INT_ET_DEMI ')'
            {
#ifndef USE_HALF_DISCS
            beep();
            syntax_error(ICMD_DEMI);
#else
        long Jn=$2, Jb=$4, n1, n2;
                double Scn=$3, Scb=$5;
                if (BAD_TOTAL(FLOAT_TO_SCORE(Scn),FLOAT_TO_SCORE(Scb)))
                        syntax_error(ICMD_TOTS);
                n1 = inscription_ID(Jn);
                n2 = inscription_ID(Jb);
                if (n1<0 || n2<0 || !present[n1] || !present[n2])
                        syntax_error(ICMD_BRES);
                else if (polarity(Jn) || polarity(Jb))
                        syntax_error(ICMD_APAIR);
                else
                        make_couple(Jn, Jb, FLOAT_TO_SCORE(Scn));
#endif
            }
        | KW_RAZ_COUPL
            { zero_coupling(); }
        | KW_TABTTR '[' INTEGER ']'
            {
                i = 0; taille_ttr = $3;
                CALLOC(table_ttr, taille_ttr, long);
            }
        '=' '{' liste_tabttr '}'
            {
                assert(table_ttr);
                if (i != taille_ttr)
                        syntax_error(TTR_TOOSMALL);
                else
                        load_rrTable(taille_ttr, table_ttr);
                free(table_ttr);
            }
        ;

liste_tabttr:   /* nothing */
        | liste_tabttr entier_signe
            {
                if (i >= taille_ttr)
                        syntax_error(TTR_TOOBIG);
                else
                        table_ttr[i++] = $2;
            }
        ;

liste_inscrits: /* nothing */
        | liste_inscrits '+' INTEGER
            {
                long n = register_player($3);
                if (n < 0) {
                        sprintf(buffer, PLR_UNKNOWN, $3);
                        syntax_error(buffer);
                } else
                        present[n] = 1;
            }
        | liste_inscrits '-' INTEGER
            {
                long n = register_player($3);
                if (n < 0) {
                        sprintf(buffer, PLR_UNKNOWN, $3);
                        syntax_error(buffer);
                } else
                        present[n] = 0;
            }
        ;

ligne_invalide: INVALID_KEYWORD error
            { syntax_error(CMD_UNKNOWN); }
        | error
            { syntax_error(ERR_SYNTAX); }
        ;

point_virgule: ';'
        | error
            { syntax_error(M_SEMICOLON); }
        ;
