/* tiebreak.h
 *
 *  - prototypes pour tiebreak.c
 *  - prototypes for tiebreak.c
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 02/02/2007 : Modification du type de '*Dep' qui passe en double
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */

#ifndef __Tiebreak_H__
#define __Tiebreak_H__


#include "global.h"
#include "discs.h"


typedef struct {
    long opp[NMAX_ROUNDS] ;        /* FFO ID of opponent */
    long opp_points[NMAX_ROUNDS] ; /* Number of points of this round opponent */
    discs_t score[NMAX_ROUNDS] ;   /* Score of the game */
    long player_points ;           /* Number of points of player (use for draws) */
} BQ_results ;


void PlayerTiebreak(BQ_results *PArray, long BQ_round, double *tiebreak, discs_t *TotalDiscsCount, long *SO) ;


#endif  /*  #ifndef __Tiebreak_h__ */
