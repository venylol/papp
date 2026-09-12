/* discs.h
 *
 *  - macros pour abstraire le type des pions dans Papp
 *  - macros to define an abstraction for discs management in Papp
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 02/02/2007 : Ajout d'une macro 'SCORE_TO_FLOAT' qui renvoie
 *                   le nombre exact de pions en flottant (pour le departage)
 *                   Addition of a macro 'FLOATING_SCORE' which returns
 *                   exact number of discs in floating points (for tiebreak)
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */

#ifndef __Discs_h__
#define __Discs_h__

#include <stdlib.h>
#include "changes.h"


extern long discsTotal;

/*
 * Les macros et fonctions generales suivantes sont abstraites
 * de la representation interne du type "discs_t"
 ****
 * Following macros and functions are abstractions of the
 * internal representation of 'discs_t' type.
 */

#define TOTAL_DISCS					(INTEGER_TO_SCORE(discsTotal))
#define UNKNOWN_SCORE				(INTEGER_TO_SCORE(-1))
#define ZERO_DISC					(INTEGER_TO_SCORE(0))

#define DIFFERENT_SCORES(v1,v2)	    (!SCORES_EQUALITY((v1),(v2)))
#define COUPON_IS_EMPTY(v)			(SCORES_EQUALITY((v),UNKNOWN_SCORE))
#define COUPON_IS_FILLED(v)	     	(SCORE_IS_POSITIVE((v)))

#define SCORE_IS_POSITIVE(v) 		(SCORE_IS_LARGER((v), ZERO_DISC))
#define SCORE_IS_LEGAL(v)			(SCORE_IS_POSITIVE(v) && (SCORE_IS_SMALLER((v),TOTAL_DISCS)))
#define IS_VICTORY(v)			    (SCORE_STRICTLY_LARGER(DOUBLE_SCORE(v), TOTAL_DISCS))
#define IS_DEFEAT(v)			    (SCORE_STRICTLY_SMALLER(DOUBLE_SCORE(v), TOTAL_DISCS))
#define IS_DRAW(v)		        	(SCORES_EQUALITY(DOUBLE_SCORE(v), TOTAL_DISCS))

#define RELATIVE_SCORE(v)			(SCORES_DIFF(DOUBLE_SCORE(v),TOTAL_DISCS))
#define OPPONENT_SCORE(v)			(SCORES_DIFF(TOTAL_DISCS, (v)))
#define SCORE_TOO_LARGE(v)			(SCORE_STRICTLY_LARGER((v),TOTAL_DISCS))
#define BAD_TOTAL(v1,v2)		    (DIFFERENT_SCORES(SCORES_SUM((v1),(v2)),TOTAL_DISCS))


/*
 * Voici maintenant la representation concrete
 * des operations sur le type "discs_t"
 * Here are concrete representation of operations
 * on type 'discs_t'
 */

#ifndef USE_HALF_DISCS

/*
 * On utilise la definition normale de PAPP des pions par des entiers,
 * et on implemente les operations par des macros.
 ****
 * Normal definition of Papp is used: discs are integer and operations are implemented by macros.
 */
	typedef long discs_t;

	#define	SCORE_IS_LARGER(v1,v2)	((v1) >= (v2))
	#define SCORE_STRICTLY_LARGER(v1,v2)	((v1) >  (v2))
	#define SCORES_EQUALITY(v1,v2)		((v1) == (v2))
	#define	SCORE_IS_SMALLER(v1,v2)		((v1) <= (v2))
	#define SCORE_STRICTLY_SMALLER(v1,v2)	((v1) <  (v2))

	#define RELATIVE_SCORE_TO_ABSOLUTE(v)  (((v) + discsTotal) / 2)
	#define INTEGER_TO_SCORE(v)          (v)
	#define FLOAT_TO_SCORE(v)        ((long)((v) + 0.49999999)) /* entier le plus proche */
	#define ADD_SCORE(v,x)			(v) += (x)
	#define SUM_SCORES(v1,v2)			((v1) + (v2))
	#define DIFF_SCORES(v1,v2)			((v1) - (v2))
	#define DOUBLE_SCORE(v)				(2*(v))
	#define	ABSOLUTE_VALUE_SCORE(v)		(abs(v))
    #define SCORE_TO_FLOAT(v)        (v)

#else

/*
 * Definition speciale pour pouvoir gerer dans Papp des scores en demi-pions:
 * on stocke les demi-pions dans une structure pour les cacher, et certaines
 * operations sont implementees par des fonctions.
 *****
 * Special definition in Papp to handle scores using half-discs.
 * They are stored in a structure to hide them and some operations
 * are implemented with functions.
 */
	typedef struct { long half_discs; } discs_t;

	#define	SCORE_IS_LARGER(v1,v2)	        ((v1).half_discs >= (v2).half_discs)
	#define SCORE_STRICTLY_LARGER(v1,v2)	((v1).half_discs >  (v2).half_discs)
	#define SCORES_EQUALITY(v1,v2)		    ((v1).half_discs == (v2).half_discs)
	#define	SCORE_IS_SMALLER(v1,v2)		    ((v1).half_discs <= (v2).half_discs)
	#define SCORE_STRICTLY_SMALLER(v1,v2)	((v1).half_discs <  (v2).half_discs)


	static discs_t INTEGER_TO_SCORE(long n)
		{	discs_t aux;
			aux.half_discs = 2*n;
			return aux; }

	static discs_t FLOAT_TO_SCORE(double n)
		{	discs_t aux;
			aux.half_discs = 2*n + 0.49999999;
			return aux; }

	static discs_t RELATIVE_SCORE_TO_ABSOLUTE(discs_t v)
		{	discs_t aux;
			aux.half_discs = (v.half_discs + INTEGER_TO_SCORE(discsTotal).half_discs) / 2 ;
			return aux; }

	static discs_t SCORES_SUM(discs_t v1, discs_t v2)
		{	discs_t aux;
			aux.half_discs = v1.half_discs + v2.half_discs;
			return aux; }

	static discs_t SCORES_DIFF(discs_t v1, discs_t v2)
		{	discs_t aux;
			aux.half_discs = v1.half_discs - v2.half_discs;
			return aux; }

	static discs_t DOUBLE_SCORE(discs_t v)
		{	discs_t aux;
			aux.half_discs = 2 * v.half_discs;
			return aux; }

	static discs_t ABSOLUTE_VALUE_SCORE(discs_t v)
		{	discs_t aux;
			aux.half_discs = labs(v.half_discs);
			return aux; }

	static double  SCORE_TO_FLOAT(discs_t v)
		{   if (v.half_discs%2 == 0)
				return (float) v.half_discs/2 ;
			else
				return ((float) v.half_discs/2) + 0.5 ;
		}

	#define ADD_SCORE(v,x)			\
		((v).half_discs) += ((x).half_discs)

#endif    /*  #else */


/*
 * fonctions pour l'affichage - Display functions
 */

char *discs2string(discs_t v);
char *tieBreak2string(double v);

/*
 * conversion d'une string en pions pour l'entree des resultats
 * conversion of a string into discs for results entry
 */

long understandScore(const char *string, discs_t *value, long *relative);


#endif  /*  #ifndef __Discs_h__ */
