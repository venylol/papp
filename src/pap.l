%{

/*
 * pap.l: Analyse lexicale pour PAPP
 *
 * (EL) 23/09/2022 : v1.37.1, small change in lex file: allow ' as first character of name
 * (EL) 03/04/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 25/06/2012 : v1.34, Aout du mot-clef 'XML' indiquant la generation des fichiers XML
 * (EL) 20/07/2008 : v1.33, Ajout de la reconnaissance de la phrase '#_Date'
                     dans le fichier intermediaire.
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, ajout de '%option noyywrap' et suppression d'un #define yywrap
                     pour eviter une erreur de compilation.
 * (EL) 29/04/2007 : v1.31. Ajout de la regle mettant de cote les caracteres indiquant
                     un codage Unicode (rajoutes par Notepad Windows par exemple)
 * (EL) 30/03/2007 : Ajout des mots-clefs 'dossier', 'true' et 'false' indiquant
                     s'il faut regrouper tous les fichiers class/ronde/result...
                     dans un meme sous-dossier portant le nom du tournoi.
 * (EL) 07/02/2007 : Ajout de la reconnaissance des phrases '#_Nom-Tournoi'
                     '#_Nombre-Rondes' et '#_BQ-double' (avec les tokens correspondants)
                     permettant de mettre ces informations dans le fichier intermediaire.
 * (EL) 04/02/2007 : Ajout du mot-clef 'BQ-double' qui donne
 *                   la constante lorsqu'on compte 1 point par victoire.
 * (EL) 04/02/2007 : initialisation des variables 'config_file_type'
 *                   et 'workfile_type' au debut de la lecture
 * (EL) 02/02/2007 : Ajout de la reconnaissance des nombres flottants et demi_entiers.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * pap.l: Lexical analysis for PAPP
 *
 * (EL) 03/04/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 25/06/2012 : v1.34, Add 'XML' keyword for XML file generation
 * (EL) 20/07/2008 : v1.33, add recognition of '#_Date' in workfile
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, Add '%option noyywrap' and remove #define yywrap to prevent a compile error
 * (EL) 29/04/2007 : v1.31, Add a rule to put aside characters indicating Unicode coding
                     (added by Notepad Windows for example)
 * (EL) 30/03/2007 : Add 'dossier', 'true' and 'false' keywords indicating
                     grouping of class/result/pairings files... in the same sub-directory
                     having tournament name as filename
 * (EL) 07/02/2007 : Add recognition of '#_Nom-Tournoi', '#_Nombre-Rondes' and '#_BQ-double'
                    (with corresponding tokens) to be able to put these infos in workfile
 * (EL) 04/02/2007 : Add 'BQ-double' keyword which gives BQ constant when 1 point is used for a victory
 * (EL) 04/02/2007 : Initialization of 'config_file_type' and 'workfile_type' variables at start of reading
 * (EL) 02/02/2007 : Add recognition of flotting and half-integers numbers
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#include "changes.h"

#ifdef __THINK_C__
# include <unix.h>
#elif defined(__BORLANDC__) || defined(PAPP_WINDOWS_METROWERKS)
# include <io.h>
#else
# include <unistd.h>
#endif

#include "pap_tab.h"
#include "global.h"

/*
 * Pour Flex : il est necessaire de caster "yytext", qui est de type "unsigned char *",
 * en un simple "char *". La definition de yywrap() permet d'eviter l'edition de liens avec libfl.a.
 ****
 * For Flex: it is necessary to cast "yytext", which is "unsigned char *" type, to a simple
 * "char *". yywrap() definition prevents linking with labfl.a library. 
 */

#define text		((char *)yytext)
/*#define yywrap()	1*/

#ifdef ENGLISH
# define BAD_KEYWORD		"unknown keyword '%s'"
# define BAD_BYTE			"found byte 0x%02x (binary data?)"
# define BAD_QUOTE			"unmatched quotes"
# define TOO_MANY_ERRS		"too many syntax errors"
# define AT_LINE			"%s, line %ld: %s\n"
#else
# define BAD_KEYWORD		"mot-cle '%s' inconnu"
# define BAD_BYTE			"caractere 0x%02x (donnees binaires?)"
# define BAD_QUOTE			"guillemets non refermes"
# define TOO_MANY_ERRS		"trop d'erreurs de syntaxe"
# define AT_LINE			"%s, ligne %ld: %s\n"
#endif

/* Etat de l'analyse lexicale - Lexical analysis state */
static long found_errors = 0;
static long line_number;
/* static */char *read_file_name;
char *current_country;

/* Table des mots-cles - keywords table */
static struct keyword {
	const char    *name;
	long     value;
} keywords[] = {
	{"fichier",                      KW_FICHIER},
	{"joueurs",                      KW_JOUEURS},
	{"nouveaux",                     KW_NOUVEAUX},
	{"intermediaire",                KW_INTER},
	{"inter",                        KW_INTER},
	{"resultats",                    KW_RESULT},
	{"classement",                   KW_CLASS},
	{"equipes",                      KW_EQUIPES},
	{"appariements",                 KW_APPARIEMENTS},
	{"tableau-croise",               KW_CROSSTABLE},
	{"elo",                          KW_ELO},
	{"log",                          KW_LOG},
	{"trace",                        KW_LOG},
	{"dossier",                      KW_DOSSIER},
	{"true",                         KW_TRUE},
	{"false",                        KW_FALSE},
	{"XML",							 KW_XML},

	{"penalites",                    KW_PENALITES},
	{"penalite",                     KW_PENALITES},
	{"pays",                         KW_PAYS},
	{"zone-insertion",               KW_ZONE_INS},
	{"brightwell",                   KW_BRIGHTWELL},
	{"BQ-double",				     KW_BRIGHTWELL_DBL},
	{"sauvegarde",                   KW_SAUVEGARDE},
	{"immediate",                    KW_SAUVIMM},
	{"differee",                     KW_SAUVDIF},
	{"impression",                   KW_IMPRESSION},
	{"manuelle",                     KW_MANUELLE},
	{"automatique",                  KW_AUTOMATIQUE},
	{"affichage-pions",              KW_AFF_PIONS},
	{"normal",                       KW_AFF_NORMAL},
	{"absolu",                       KW_AFF_NORMAL},
	{"differentiel",                 KW_AFF_DIFF},
	{"relatif",                      KW_AFF_DIFF},
	{"score-bip",                    KW_SCORE_BIP},
	{"ronde-suivante",               KW_RONDESUIV},
	{"raz-couplage",                 KW_RAZ_COUPL},
	{"toutes-rondes",                KW_TTRONDES},
	{"table-toutes-rondes",          KW_TABTTR},
	{"round-robin-table",            KW_TABTTR},

	{"fois",                         KW_FOIS},
	{"demi-point",                   KW_DPOINT},
	{"demi-points",                  KW_DPOINT},
	{"couleur",                      KW_COULEUR},
	{"couleurs",                     KW_COULEUR},
	{"flottement",                   KW_FLOTTEMENT},
	{"cumule",                       KW_DESUITE},
	{"minoration",                   KW_MINORATION},
	{"anti-cumule",                  KW_MINORATION},
	{"elitisme",                     KW_ELITISME},
	{"repetition",                   KW_REPET},
	{"memes-couleurs",               KW_MCOUL},
	{"couleurs-opposees",            KW_COPP},
	{"de-suite",                     KW_DESUITE},
	{"bip-bip",                      KW_BIPBIP},
	{"chauvinisme",                  KW_CHAUVIN},
	{"ronde",                        KW_RONDE},
	{"rondes",                       KW_RONDE},

	{(char*) 0,                      0}
}, *kw;

%}
%option noyywrap


UTF_BOM (\xEF\xBB\xBF)|((\x00\x00)?(\xFE\xFF))|((\xFF\xFE)(\x00\x00)?)|(\x0E\xFE\xFF)|(\xFB\xEE\x28)|(\xDD\x73\x66\x73)|((\x2B\x2F\x76)([\x38\x39\x2B\x2F]|(\x38\x2D)))
CRLF \x0D\x0A
EOL [\x0A\x0D]
NOT_EOL [^\x0A\x0D]

%s UNQUOTED

%%

__eof__         return 0;

{UTF_BOM}       ;
[0-9]+          { yylval.integer = strtol(text, NULL, 10); return INTEGER; }
[0-9]+\.[05]    { yylval.reel    = atof(text); return INT_ET_DEMI; }
[0-9]*\.[0-9]+  { yylval.reel    = atof(text); return DOUBLE; }
[ \t\000]+      ;
\%              ;
\%_             ;
\%[^_!\x0A\x0D]{NOT_EOL}*   ;
#               ;
#[^_\x0A\x0D]{NOT_EOL}*       ;
#_Nom-Tournoi   { return DIESE_NOM ; }
#_Nombre-Rondes { return DIESE_RONDES ; }
#_BQ-double     { return DIESE_BRIGHTWELL_DBL ; }
#_Date          { return DIESE_DATE ; }
\%!{NOT_EOL}*           { fprintf(stderr, "%s\n", text); }

\"[^\x0A\x0D\"]*\"    {
		yylval.string = strcpy(new_string(), text+1);
		yylval.string[yyleng-2] = 0;
		return STRING;
		}

\"[^\x0A\x0D\"]*{EOL}    {
		syntax_error(BAD_QUOTE);
		++line_number;
		yylval.string = strcpy(new_string(), text+1);
		yylval.string[yyleng-2] = 0;
		return STRING;
		}

<UNQUOTED>{CRLF}[Pp]ays    { ++line_number; return KW_PAYS; }
<UNQUOTED>{EOL}[Pp]ays     { ++line_number; return KW_PAYS; }
<UNQUOTED>^[Pp]ays          return KW_PAYS;
<UNQUOTED>{CRLF}[Ff]ederation{NOT_EOL}*   {++line_number;}
<UNQUOTED>{EOL}[Ff]ederation{NOT_EOL}*    {++line_number;}
<UNQUOTED>^[Ff]ederation{NOT_EOL}*         ;

<INITIAL>{CRLF}    { ++line_number; }
<INITIAL>{EOL}     { ++line_number; }
<UNQUOTED>{CRLF}   { ++line_number; return TOK_NEWLINE; }
<UNQUOTED>{EOL}    { ++line_number; return TOK_NEWLINE; }

<UNQUOTED>[a-z_A-Z'][a-z_A-Z'0-9\-\.]* {
		yylval.string = strcpy(new_string(), text);
		return STRING;
		}

<UNQUOTED>'[^\x0A\x0D\000]*	{
		yylval.string = strcpy(new_string(), text+1);
		return COMMENT;
		}

<INITIAL>[a-zA-Z][a-zA-Z\-]* {
		/* chercher le mot dans la table - lookup keyword in table */
		for (kw = keywords; kw->name; kw++)
			if (!compare_strings_insensitive(kw->name,text))
				return kw->value;
		/* il n'y est pas - not in table */
		if (!compare_strings_insensitive("infini",text)) {
			yylval.integer = LONG_MAX;
			return INTEGER;
		} else {
			char *buf = new_string();
			sprintf(buf, BAD_KEYWORD, text);
			syntax_error(buf);
			return INVALID_KEYWORD;
			}
		}

.               {
		if (yytext[0] & 0x80) {
			char *buf = new_string();
			sprintf(buf, BAD_BYTE, yytext[0]);
			syntax_error(buf);
		}
		return yytext[0];
		}




%%

/*
 *  Fonction de conversion des CR du Mac en CR/LF (en conservant les CR/LF du DOS)
 *  pour la version PC de Papp, car le parser genere par Flex sur PC n'aime pas les lignes tres longues.
 *  Le fichier d'origine n'est pas touche, on cree un fichier temporaire qu'il faudra penser a effacer
 *  apres l'avoir parse.  Retourne 0 si tout se passe bien, -1 sinon.
 ****
 *  Conversion function between Vintage MacOS CR into CR/LF (while keeping DOS CR/LF) for PC version of Papp
 *  because flex parser on PC doesn't like long lines. Original file isn't changed, a temporary file is
 *  created and must be removed after parsing. Returns 0 if all goes well, -1 otherwise.
 */


long Macintosh_to_PC(char *filename, char **tempfile) {
	FILE *old,*temp;
	long c1,c2;
    char temp_name[]="temp.XXXXXX" ;

    *tempfile = "";
	old = fopen( filename, "rb" );
	if ( old == NULL )
		return -1;

	/* On essaie d'avoir un nom temporaire - trying to get temporary filename */
	if (mkstemp(temp_name) == -1)
		return -1;

	temp = fopen( temp_name, "wb" );
	if ( temp == NULL )
		return -1;
	c1 = fgetc(old);
    if ( c1 == EOF )
    	return -1;

	c2 = 0;
	while( (c2=fgetc(old)) != EOF ) {
	    if ((c1 == 13) && (c2 != 10)) {
	       fputc( 13, temp );
	       fputc( 10, temp );
	    } else
	       fputc( c1, temp );

	    c1 = c2;
	}

	fputc(c1,temp);
	if (c1 == 13)
		fputc(10,temp);
	fclose( old );
	fclose( temp );
	strcpy(*tempfile,temp_name);
	return 0;
}

/*
 * Fonction de lecture;
 *   type==0 pour le fichier de config,
 *   type==1 pour un fichier de joueurs.
 ****
 * Read function
 *  type==0 for configuration file
 *  type==1 for players file
 */


/*
 * Sur les anciens Macintosh, les fonctions de lecture de la librairie standard sont trop intelligentes
 * pour les fichiers textes et inversent carrement les CR et les LF ! On ouvre donc les fichiers en binaire.
 * Question : est-ce que les parseurs marchent encore sur les autres plate-formes si on fait la meme chose ?
 ****
 * On old Macintosh platforms, standard library reading functions are too intelligent for text files and
 * swap CR and LF! Files are therefore opened in binary mode.
 * Question: do parsers still work on other platforms if we do the same?
 */

#if defined(__THINK_C__) || defined(PAPP_MAC_METROWERKS)
   #define FILE_OPENING_MODE "rb"
#else
   #define FILE_OPENING_MODE "r"
#endif

long read_file(char *filename, long type) {
	static long nbr_reads = 0;
	long ret;
#ifdef DUPLIQUE_FICHIER_PARSER
	long use_temp_file;
	char *tempfile;
#endif

	assert(type == CONFIG_F || type == PLAYERS_F || type == NEWPLAYERS_F);
#ifdef DEBUG
	fprintf(stderr,"Reading '%s' (%s)\n", filename,
		type == CONFIG_F ? "config" : "players or newPlayers");
#endif

#ifdef DUPLIQUE_FICHIER_PARSER
    tempfile = trivialloc(250);
    if (Macintosh_to_PC(filename,&tempfile) == 0) {
       yyin = fopen(tempfile, FILE_OPENING_MODE);
       use_temp_file = 1;
    } else {
       yyin = fopen(filename, FILE_OPENING_MODE);
       use_temp_file = 0;
    }
#else
    yyin = fopen(filename, FILE_OPENING_MODE);
#endif

	if (yyin == NULL)
		return -1;
	if (nbr_reads++)
		yyrestart(yyin);
	found_errors = 0;
	line_number = 1;
	read_file_name = filename;
	current_country = default_country;
	/* Type d'analyse lexicale? - Which lexical analysis type? */
	if (type == CONFIG_F) {
		BEGIN(INITIAL);
		config_file_type = CORRECT ;
		workfile_type = OLD ;
	} else {
		BEGIN(UNQUOTED) ;
		new_players_file = ((type == NEWPLAYERS_F) ? 1 : 0) ;
	}
	ret = yyparse();
	fclose(yyin);

#ifdef DUPLIQUE_FICHIER_PARSER
    if (use_temp_file)
       { remove(tempfile); }
#endif

    if (type == CONFIG_F)
		default_country = current_country;
	return (ret + found_errors);
}

/* Fonctions d'erreur - Error functions */

void syntax_error(const char *error) {
	syntax_warning(error);
	if (++found_errors >= 15)
		fatal_error(TOO_MANY_ERRS);
}

void syntax_warning(const char *error) {
	fprintf(stderr, AT_LINE, read_file_name, line_number, error);
}
