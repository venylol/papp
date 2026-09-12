/* A Bison parser, made by GNU Bison 2.3.  */

/* Skeleton interface for Bison's Yacc-like parsers in C

   Copyright (C) 1984, 1989, 1990, 2000, 2001, 2002, 2003, 2004, 2005, 2006
   Free Software Foundation, Inc.

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2, or (at your option)
   any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 51 Franklin Street, Fifth Floor,
   Boston, MA 02110-1301, USA.  */

/* As a special exception, you may create a larger work that contains
   part or all of the Bison parser skeleton and distribute that work
   under terms of your choice, so long as that work isn't itself a
   parser generator using the skeleton or a modified version thereof
   as a parser skeleton.  Alternatively, if you modify or redistribute
   the parser skeleton itself, you may (at your option) remove this
   special exception, which will cause the skeleton and the resulting
   Bison output files to be licensed under the GNU General Public
   License without this special exception.

   This special exception was added by the Free Software Foundation in
   version 2.2 of Bison.  */

/* Tokens.  */
#ifndef YYTOKENTYPE
# define YYTOKENTYPE
   /* Put the tokens into the symbol table, so that GDB and other debuggers
      know about them.  */
   enum yytokentype {
     TOK_NEWLINE = 258,
     KW_RAZ_COUPL = 259,
     KW_SCORE_BIP = 260,
     KW_COPP = 261,
     KW_TTRONDES = 262,
     KW_TABTTR = 263,
     INVALID_KEYWORD = 264,
     KW_SAUVEGARDE = 265,
     KW_SAUVIMM = 266,
     KW_SAUVDIF = 267,
     KW_BIPBIP = 268,
     KW_IMPRESSION = 269,
     KW_MANUELLE = 270,
     KW_AUTOMATIQUE = 271,
     KW_MINORATION = 272,
     KW_AFF_PIONS = 273,
     KW_AFF_NORMAL = 274,
     KW_AFF_DIFF = 275,
     KW_FICHIER = 276,
     KW_JOUEURS = 277,
     KW_NOUVEAUX = 278,
     KW_INTER = 279,
     KW_LOG = 280,
     KW_RESULT = 281,
     KW_CLASS = 282,
     KW_EQUIPES = 283,
     KW_APPARIEMENTS = 284,
     KW_CROSSTABLE = 285,
     KW_ELO = 286,
     KW_PENALITES = 287,
     KW_PAYS = 288,
     KW_ZONE_INS = 289,
     KW_FOIS = 290,
     KW_DPOINT = 291,
     KW_COULEUR = 292,
     KW_FLOTTEMENT = 293,
     KW_REPET = 294,
     KW_MCOUL = 295,
     KW_DESUITE = 296,
     KW_RONDESUIV = 297,
     KW_CHAUVIN = 298,
     KW_RONDE = 299,
     KW_ELITISME = 300,
     KW_BRIGHTWELL = 301,
     KW_BRIGHTWELL_DBL = 302,
     KW_DOSSIER = 303,
     KW_XML = 304,
     KW_TRUE = 305,
     KW_FALSE = 306,
     DIESE_NOM = 307,
     DIESE_RONDES = 308,
     DIESE_BRIGHTWELL_DBL = 309,
     DIESE_DATE = 310,
     STRING = 311,
     COMMENT = 312,
     INTEGER = 313,
     DOUBLE = 314,
     INT_ET_DEMI = 315
   };
#endif
/* Tokens.  */
#define TOK_NEWLINE 258
#define KW_RAZ_COUPL 259
#define KW_SCORE_BIP 260
#define KW_COPP 261
#define KW_TTRONDES 262
#define KW_TABTTR 263
#define INVALID_KEYWORD 264
#define KW_SAUVEGARDE 265
#define KW_SAUVIMM 266
#define KW_SAUVDIF 267
#define KW_BIPBIP 268
#define KW_IMPRESSION 269
#define KW_MANUELLE 270
#define KW_AUTOMATIQUE 271
#define KW_MINORATION 272
#define KW_AFF_PIONS 273
#define KW_AFF_NORMAL 274
#define KW_AFF_DIFF 275
#define KW_FICHIER 276
#define KW_JOUEURS 277
#define KW_NOUVEAUX 278
#define KW_INTER 279
#define KW_LOG 280
#define KW_RESULT 281
#define KW_CLASS 282
#define KW_EQUIPES 283
#define KW_APPARIEMENTS 284
#define KW_CROSSTABLE 285
#define KW_ELO 286
#define KW_PENALITES 287
#define KW_PAYS 288
#define KW_ZONE_INS 289
#define KW_FOIS 290
#define KW_DPOINT 291
#define KW_COULEUR 292
#define KW_FLOTTEMENT 293
#define KW_REPET 294
#define KW_MCOUL 295
#define KW_DESUITE 296
#define KW_RONDESUIV 297
#define KW_CHAUVIN 298
#define KW_RONDE 299
#define KW_ELITISME 300
#define KW_BRIGHTWELL 301
#define KW_BRIGHTWELL_DBL 302
#define KW_DOSSIER 303
#define KW_XML 304
#define KW_TRUE 305
#define KW_FALSE 306
#define DIESE_NOM 307
#define DIESE_RONDES 308
#define DIESE_BRIGHTWELL_DBL 309
#define DIESE_DATE 310
#define STRING 311
#define COMMENT 312
#define INTEGER 313
#define DOUBLE 314
#define INT_ET_DEMI 315




#if ! defined YYSTYPE && ! defined YYSTYPE_IS_DECLARED
typedef union YYSTYPE
#line 217 "pap.y"
{
        long integer;
        char *string;
        double reel;
        }
/* Line 1529 of yacc.c.  */
#line 175 "pap.tab.h"
	YYSTYPE;
# define yystype YYSTYPE /* obsolescent; will be withdrawn */
# define YYSTYPE_IS_DECLARED 1
# define YYSTYPE_IS_TRIVIAL 1
#endif

extern YYSTYPE yylval;

