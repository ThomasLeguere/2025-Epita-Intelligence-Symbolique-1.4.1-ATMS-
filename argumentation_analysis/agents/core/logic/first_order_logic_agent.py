# argumentation_analysis/agents/core/logic/first_order_logic_agent.py
"""
Agent spécialisé pour la logique du premier ordre (FOL).

Ce module définit `FirstOrderLogicAgent`, une classe qui hérite de
`BaseLogicAgent` et implémente les fonctionnalités spécifiques pour interagir
avec la logique du premier ordre. Il utilise `TweetyBridge` pour la communication
avec TweetyProject et s'appuie sur des prompts sémantiques définis dans ce
module pour la conversion texte-vers-FOL, la génération de requêtes et
l'interprétation des résultats.
"""

import logging
import re
import json
import jpype
from typing import Dict, List, Optional, Any, Tuple, AsyncGenerator

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.contents import ChatMessageContent
from semantic_kernel.contents.chat_history import ChatHistory
from pydantic import Field

from ..abc.agent_bases import BaseLogicAgent
from .belief_set import BeliefSet, FirstOrderBeliefSet
from .tweety_bridge import TweetyBridge

# Configuration du logger
logger = logging.getLogger(__name__) # Utilisation de __name__ pour une meilleure pratique

# Prompt Système pour l'agent FOL
SYSTEM_PROMPT_FOL = """Vous êtes un agent spécialisé dans l'analyse et le raisonnement en logique du premier ordre (FOL).
Vous utilisez la syntaxe de TweetyProject pour représenter les formules FOL.
Vos tâches principales incluent la traduction de texte en formules FOL, la génération de requêtes FOL pertinentes,
l'exécution de ces requêtes sur un ensemble de croyances FOL, et l'interprétation des résultats obtenus.
"""
"""
Prompt système pour l'agent de logique du premier ordre.
Définit le rôle et les capacités générales de l'agent pour le LLM.
"""

# Prompts pour la logique du premier ordre (optimisés)
PROMPT_TEXT_TO_FOL_DEFS = """Expert FOL : Extrayez sorts et prédicats du texte en format JSON strict.

Format : {"sorts": {"type": ["const1", "const2"]}, "predicates": [{"name": "PredName", "args": ["type1"]}]}

Règles : sorts/constantes en snake_case, prédicats commencent par majuscule.

Exemple : "Jean aime Paris" → {"sorts": {"person": ["jean"], "place": ["paris"]}, "predicates": [{"name": "Loves", "args": ["person", "place"]}]}

Texte : {{$input}}
"""
"""
Prompt pour extraire les sorts et prédicats d'un texte.
Attend `$input` (le texte source).
"""

PROMPT_TEXT_TO_FOL_FORMULAS = """Expert FOL : Traduisez le texte en formules FOL en JSON strict.

Format : {"formulas": ["Pred(const)", "forall X: (Pred1(X) => Pred2(X))"]}

Règles : Utilisez UNIQUEMENT les sorts/prédicats fournis. Variables majuscules (X,Y). Connecteurs : !, &&, ||, =>, <=>

Texte : {{$input}}
Définitions : {{$definitions}}
"""
"""
Prompt pour générer des formules FOL à partir d'un texte et de définitions.
Attend `$input` (texte source) et `$definitions` (JSON des sorts et prédicats).
"""

PROMPT_GEN_FOL_QUERIES_IDEAS = """Expert FOL : Générez des requêtes pertinentes en JSON strict.

Format : {"query_ideas": [{"predicate_name": "PredName", "constants": ["const1"]}]}

Règles : Utilisez UNIQUEMENT les prédicats/constantes du belief set. Priorité aux requêtes vérifiables.

Texte : {{$input}}
Belief Set : {{$belief_set}}
"""
"""
Prompt pour générer des idées de requêtes FOL au format JSON.
Attend `$input` (texte source) et `$belief_set` (l'ensemble de croyances FOL).
"""

PROMPT_INTERPRET_FOL = """Expert FOL : Interprétez les résultats de requêtes FOL en langage accessible.

Texte : {{$input}}
Belief Set : {{$belief_set}}
Requêtes : {{$queries}}
Résultats : {{$tweety_result}}

Pour chaque requête : objectif, statut (ACCEPTED/REJECTED), signification, implications.
Conclusion générale concise.
"""
"""
Prompt pour interpréter les résultats de requêtes FOL en langage naturel.
Attend `$input` (texte source), `$belief_set` (ensemble de croyances FOL),
`$queries` (les requêtes exécutées), et `$tweety_result` (les résultats bruts de Tweety).
"""

class FirstOrderLogicAgent(BaseLogicAgent): 
    """
    Agent spécialisé pour la logique du premier ordre (FOL).

    Cet agent étend `BaseLogicAgent` pour fournir des capacités de traitement
    spécifiques à la logique du premier ordre. Il intègre des fonctions sémantiques
    pour traduire le langage naturel en ensembles de croyances FOL, générer des
    requêtes FOL pertinentes, exécuter ces requêtes via `TweetyBridge`, et
    interpréter les résultats en langage naturel.

    Attributes:
        _tweety_bridge (TweetyBridge): Instance de `TweetyBridge` configurée pour la FOL.
    """
    
    # Attributs requis par Pydantic V2 pour la nouvelle classe de base Agent
    # Ces attributs ne sont pas utilisés activement par cette classe, mais doivent être déclarés.
    # Ils sont normalement gérés par la classe de base `Agent` mais Pydantic exige leur présence.
    # Utilisation de `Field(default=..., exclude=True)` pour éviter qu'ils soient inclus
    # dans la sérialisation ou la validation standard du modèle.
    service: Optional[ChatCompletionClientBase] = Field(default=None, exclude=True)
    settings: Optional[Any] = Field(default=None, exclude=True) # Remplace PromptExecutionSettings

    def __init__(self, kernel: Kernel, agent_name: str = "FirstOrderLogicAgent", service_id: Optional[str] = None):
        """
        Initialise une instance de `FirstOrderLogicAgent`.

        :param kernel: Le kernel Semantic Kernel à utiliser pour les fonctions sémantiques.
        :param agent_name: Le nom de l'agent (par défaut "FirstOrderLogicAgent").
        :param service_id: L'ID du service LLM à utiliser.
        """
        super().__init__(
            kernel=kernel,
            agent_name=agent_name,
            logic_type_name="FOL",
            system_prompt=SYSTEM_PROMPT_FOL
        )
        self._llm_service_id = service_id

    def get_agent_capabilities(self) -> Dict[str, Any]:
        """
        Retourne un dictionnaire décrivant les capacités spécifiques de cet agent FOL.

        :return: Un dictionnaire détaillant le nom, le type de logique, la description
                 et les méthodes de l'agent.
        :rtype: Dict[str, Any]
        """
        return {
            "name": self.name,
            "logic_type": self.logic_type,
            "description": "Agent capable d'analyser du texte en utilisant la logique du premier ordre (FOL). "
                           "Peut convertir du texte en un ensemble de croyances FOL, générer des requêtes FOL, "
                           "exécuter ces requêtes, et interpréter les résultats.",
            "methods": {
                "text_to_belief_set": "Convertit un texte en un ensemble de croyances FOL.",
                "generate_queries": "Génère des requêtes FOL pertinentes à partir d'un texte et d'un ensemble de croyances.",
                "execute_query": "Exécute une requête FOL sur un ensemble de croyances.",
                "interpret_results": "Interprète les résultats d'une ou plusieurs requêtes FOL.",
                "validate_formula": "Valide la syntaxe d'une formule FOL."
            }
        }

    def setup_agent_components(self, llm_service_id: str) -> None:
        """
        Configure les composants spécifiques de l'agent de logique du premier ordre.

        Initialise `TweetyBridge` pour la logique FOL et enregistre les fonctions
        sémantiques nécessaires (TextToFOLBeliefSet, GenerateFOLQueries,
        InterpretFOLResult) dans le kernel Semantic Kernel.

        :param llm_service_id: L'ID du service LLM à utiliser pour les fonctions sémantiques.
        :type llm_service_id: str
        """
        super().setup_agent_components(llm_service_id)
        self.logger.info(f"Configuration des composants pour {self.name}...")

        self._tweety_bridge = TweetyBridge()

        if not self.tweety_bridge.is_jvm_ready():
            self.logger.error("Tentative de setup FOL Kernel alors que la JVM n'est PAS démarrée.")
            return
        
        default_settings = None
        if self._llm_service_id: 
            try:
                default_settings = self.sk_kernel.get_prompt_execution_settings_from_service_id(
                    self._llm_service_id
                )
                self.logger.debug(f"Settings LLM récupérés pour {self.name}.")
            except Exception as e:
                self.logger.warning(f"Impossible de récupérer settings LLM pour {self.name}: {e}")

        semantic_functions = [
            ("TextToFOLDefs", PROMPT_TEXT_TO_FOL_DEFS,
             "Extrait les sorts et prédicats FOL d'un texte."),
            ("TextToFOLFormulas", PROMPT_TEXT_TO_FOL_FORMULAS,
             "Génère les formules FOL à partir d'un texte et de définitions."),
            ("GenerateFOLQueryIdeas", PROMPT_GEN_FOL_QUERIES_IDEAS,
             "Génère des idées de requêtes FOL au format JSON."),
            ("InterpretFOLResult", PROMPT_INTERPRET_FOL,
             "Interprète résultat requête FOL Tweety formaté.")
        ]

        for func_name, prompt, description in semantic_functions:
            try:
                if not prompt or not isinstance(prompt, str):
                    self.logger.error(f"ERREUR: Prompt invalide pour {self.name}.{func_name}")
                    continue
                
                self.logger.info(f"Ajout fonction {self.name}.{func_name} avec prompt de {len(prompt)} caractères")
                self.sk_kernel.add_function(
                    prompt=prompt,
                    plugin_name=self.name, 
                    function_name=func_name,
                    description=description,
                    prompt_execution_settings=default_settings
                )
                self.logger.debug(f"Fonction sémantique {self.name}.{func_name} ajoutée/mise à jour.")
                
                if self.name in self.sk_kernel.plugins and func_name in self.sk_kernel.plugins[self.name]:
                    self.logger.info(f"(OK) Fonction {self.name}.{func_name} correctement enregistrée.")
                else:
                    self.logger.error(f"(CRITICAL ERROR) Fonction {self.name}.{func_name} non trouvée après ajout!")
            except ValueError as ve:
                self.logger.warning(f"Problème ajout/MàJ {self.name}.{func_name}: {ve}")
            except Exception as e:
                self.logger.error(f"Exception inattendue lors de l'ajout de {self.name}.{func_name}: {e}", exc_info=True)
        
        self.logger.info(f"Composants de {self.name} configurés.")

    def _construct_kb_from_json(self, kb_json: Dict[str, Any]) -> str:
        """
        Construit une base de connaissances FOL textuelle à partir d'un JSON structuré,
        en respectant la syntaxe BNF de TweetyProject.
        """
        kb_parts = []

        # 1. SORTSDEC
        sorts = kb_json.get("sorts", {})
        if sorts:
            for sort_name, constants in sorts.items():
                if constants:
                    kb_parts.append(f"{sort_name} = {{ {', '.join(constants)} }}")

        # 2. DECLAR (PREDDEC)
        predicates = kb_json.get("predicates", [])
        if predicates:
            for pred in predicates:
                pred_name = pred.get("name")
                args = pred.get("args", [])
                if pred_name:
                    args_str = f"({', '.join(args)})" if args else ""
                    kb_parts.append(f"type({pred_name}{args_str})")

        # 3. FORMULAS
        formulas = kb_json.get("formulas", [])
        if formulas:
            # Assurer que les formules sont bien séparées des déclarations
            if kb_parts:
                kb_parts.append("\n")
            # Nettoyer les formules en enlevant le point-virgule final si présent
            cleaned_formulas = [f.strip().removesuffix(';') for f in formulas]
            kb_parts.extend(cleaned_formulas)

        return "\n".join(kb_parts)

    def _normalize_identifier(self, text: str) -> str:
        """Normalise un identifiant en snake_case sans accents."""
        import unidecode
        text = unidecode.unidecode(text) # Translitère les accents (é -> e)
        text = re.sub(r'\s+', '_', text) # Remplace les espaces par des underscores
        text = re.sub(r'[^a-zA-Z0-9_]', '', text) # Supprime les caractères non alphanumériques
        return text.lower()

    def _validate_kb_json(self, kb_json: Dict[str, Any]) -> Tuple[bool, str]:
        """Valide la cohérence interne du JSON généré par le LLM."""
        if not all(k in kb_json for k in ["sorts", "predicates", "formulas"]):
            return False, "Le JSON doit contenir les clés 'sorts', 'predicates', et 'formulas'."

        declared_constants = set()
        for constants_list in kb_json.get("sorts", {}).values():
            if isinstance(constants_list, list):
                declared_constants.update(constants_list)
        
        declared_predicates = {p["name"]: len(p.get("args", [])) for p in kb_json.get("predicates", []) if "name" in p}

        for formula in kb_json.get("formulas", []):
            quantified_vars = set(re.findall(r'(?:forall|exists)\s+([A-Z][a-zA-Z0-9_]*)\s*:', formula))
            used_predicates = re.findall(r'([A-Z][a-zA-Z0-9]*)\((.*?)\)', formula)
            for pred_name, args_str in used_predicates:
                if pred_name not in declared_predicates:
                    return False, f"Le prédicat '{pred_name}' utilisé dans la formule '{formula}' n'est pas déclaré."
                
                args_list = [arg.strip() for arg in args_str.split(',')] if args_str.strip() else []
                used_arity = len(args_list)
                if declared_predicates[pred_name] != used_arity:
                    return False, f"Incohérence d'arité pour '{pred_name}'. Déclaré: {declared_predicates[pred_name]}, utilisé: {used_arity} dans '{formula}'."

                for arg in args_list:
                    if not arg: continue
                    # Si l'argument commence par une minuscule, c'est une constante
                    if arg[0].islower():
                        if arg not in declared_constants:
                            return False, f"La constante '{arg}' utilisée dans la formule '{formula}' n'est pas déclarée dans les 'sorts'."
                    # Si l'argument commence par une majuscule, c'est une variable
                    elif arg[0].isupper():
                        if arg not in quantified_vars:
                            return False, f"La variable '{arg}' utilisée dans la formule '{formula}' n'est pas liée par un quantificateur (forall/exists)."

        return True, "Validation du JSON réussie."

    async def text_to_belief_set(self, text: str, context: Optional[Dict[str, Any]] = None) -> Tuple[Optional[BeliefSet], str]:
        """
        Convertit un texte en langage naturel en un ensemble de croyances FOL en plusieurs étapes.
        1. Génère les sorts et prédicats.
        2. Corrige programmatiquement les types d'arguments des prédicats.
        3. Génère les formules en se basant sur les définitions corrigées.
        4. Assemble et valide le tout.
        """
        self.logger.info(f"Conversion de texte en ensemble de croyances FOL pour {self.name} (approche en plusieurs étapes)...")
        
        max_retries = 3
        last_error = ""
        
        # Variables pour stocker les parties de la base de connaissances
        defs_json = None
        formulas_json = None
        
        # --- Étape 1: Génération des Définitions (Sorts et Prédicats) ---
        self.logger.info("Étape 1: Génération des sorts et prédicats...")
        try:
            defs_result = await self.sk_kernel.plugins[self.name]["TextToFOLDefs"].invoke(self.sk_kernel, input=text)
            defs_json_str = self._extract_json_block(str(defs_result))
            defs_json = json.loads(defs_json_str)
            self.logger.info("Sorts et prédicats générés avec succès.")
        except (json.JSONDecodeError, Exception) as e:
            error_msg = f"Échec de la génération des définitions (sorts/prédicats): {e}"
            self.logger.error(error_msg)
            return None, error_msg

        # --- Étape 1.5: Correction programmatique des types de prédicats ---
        self.logger.info("Étape 1.5: Correction des arguments des prédicats...")
        try:
            # Créer une structure inversée mappant chaque constante à son sort.
            sorts_map = {
                constant: sort_name
                for sort_name, constants in defs_json.get("sorts", {}).items()
                for constant in constants
            }
            
            corrected_predicates = []
            predicates_to_correct = defs_json.get("predicates", [])
            self.logger.debug(f"Prédicats avant correction: {json.dumps(predicates_to_correct, indent=2)}")

            for predicate in predicates_to_correct:
                corrected_args = []
                # Itérer sur les arguments de chaque prédicat.
                for arg in predicate.get("args", []):
                    # Si l'argument n'est pas un sort valide, il est considéré comme une constante.
                    if arg not in defs_json.get("sorts", {}):
                        # Trouver le sort correct pour cette constante.
                        correct_sort = sorts_map.get(arg)
                        if correct_sort:
                            self.logger.debug(f"Correction: Remplacement de la constante '{arg}' par le sort '{correct_sort}' dans le prédicat '{predicate['name']}'.")
                            corrected_args.append(correct_sort)
                        else:
                            # Si la constante n'est mappée à aucun sort, conserver l'original et logger un avertissement.
                            self.logger.warning(f"Impossible de trouver un sort pour la constante '{arg}' dans le prédicat '{predicate['name']}'. Argument conservé.")
                            corrected_args.append(arg)
                    else:
                        # L'argument est déjà un sort valide.
                        corrected_args.append(arg)
                
                # Créer le prédicat corrigé.
                corrected_predicate = {"name": predicate["name"], "args": corrected_args}
                corrected_predicates.append(corrected_predicate)
            
            # Mettre à jour le JSON des définitions avec la liste des prédicats corrigée.
            defs_json["predicates"] = corrected_predicates
            self.logger.debug(f"Prédicats après correction: {json.dumps(corrected_predicates, indent=2)}")
            self.logger.info("Correction des prédicats terminée.")

        except Exception as e:
            error_msg = f"Échec de l'étape de correction des prédicats: {e}"
            self.logger.error(error_msg, exc_info=True)
            return None, error_msg

        # --- Étape 2: Génération des Formules ---
        self.logger.info("Étape 2: Génération des formules...")
        try:
            definitions_for_prompt = json.dumps(defs_json, indent=2)
            self.logger.debug(f"Prompt complet pour TextToFOLFormulas (avec définitions corrigées):\nInput: {text}\nDefinitions:\n{definitions_for_prompt}")
            formulas_result = await self.sk_kernel.plugins[self.name]["TextToFOLFormulas"].invoke(
                self.sk_kernel, input=text, definitions=definitions_for_prompt
            )
            formulas_json_str = self._extract_json_block(str(formulas_result))
            formulas_json = json.loads(formulas_json_str)
            self.logger.info("Formules générées avec succès.")
        except (json.JSONDecodeError, Exception) as e:
            error_msg = f"Échec de la génération des formules: {e}"
            self.logger.error(error_msg)
            return None, error_msg

        # --- Étape 3: Assemblage, Validation et Correction ---
        self.logger.info("Étape 3: Assemblage, validation et correction...")
        kb_json = {
            "sorts": defs_json.get("sorts", {}),
            "predicates": defs_json.get("predicates", []),
            "formulas": formulas_json.get("formulas", [])
        }
        
        # --- Étape 4: Filtrage impitoyable des formules ---
        self.logger.info("Étape 4: Filtrage des formules basé sur les constantes déclarées...")
        try:
            # Créer un ensemble de toutes les constantes valides déclarées
            valid_constants = set()
            for sort_name, constants in kb_json.get("sorts", {}).items():
                valid_constants.update(constants)
            self.logger.debug(f"Constantes valides déclarées: {valid_constants}")

            # Filtrer les formules
            valid_formulas = []
            original_formula_count = len(kb_json.get("formulas", []))
            
            for formula in kb_json.get("formulas", []):
                # Extraire tous les termes potentiels (mots en minuscules, snake_case)
                # Cette regex cible les identifiants qui ne sont pas des noms de prédicats (commençant par une majuscule)
                # et qui ne sont pas des mots-clés logiques.
                terms = re.findall(r'\b[a-z_][a-z0-9_]*\b', formula)
                
                # Vérifier si tous les termes extraits sont des constantes valides
                is_formula_valid = True
                for term in terms:
                    if term not in valid_constants:
                        self.logger.info(f"Formule rejetée: Terme invalide '{term}' trouvé dans '{formula}'.")
                        is_formula_valid = False
                        break
                
                if is_formula_valid:
                    valid_formulas.append(formula)
                
            # Remplacer la liste de formules par la liste filtrée
            kb_json["formulas"] = valid_formulas
            filtered_formula_count = len(valid_formulas)
            self.logger.info(f"Filtrage terminé. {filtered_formula_count}/{original_formula_count} formules conservées.")

        except Exception as e:
            error_msg = f"Échec de l'étape de filtrage des formules: {e}"
            self.logger.error(error_msg, exc_info=True)
            return None, error_msg

        current_kb_json = kb_json
        
        for attempt in range(max_retries):
            self.logger.info(f"Tentative de validation et correction {attempt + 1}/{max_retries}...")
            
            try:
                # 1. Normaliser et valider la cohérence du JSON assemblé
                normalized_kb_json = self._normalize_and_validate_json(current_kb_json)
                
                # 2. Construire la base de connaissances
                full_belief_set_content = self._construct_kb_from_json(normalized_kb_json)
                if not full_belief_set_content:
                    raise ValueError("La conversion a produit une base de connaissances vide.")

                # 3. Valider le belief set complet avec Tweety
                is_valid, validation_msg = self.tweety_bridge.validate_fol_belief_set(full_belief_set_content)
                if not is_valid:
                    raise ValueError(f"Ensemble de croyances invalide selon Tweety: {validation_msg}\nContenu:\n{full_belief_set_content}")

                belief_set_obj = FirstOrderBeliefSet(full_belief_set_content)
                self.logger.info("Conversion et validation réussies.")
                return belief_set_obj, "Conversion réussie"

            except (ValueError, jpype.JException) as e:
                last_error = f"Validation ou erreur de syntaxe FOL: {e}"
                self.logger.warning(f"{last_error} à la tentative {attempt + 1}")
                
                # Pour l'instant, on ne tente pas de boucle de correction automatique complexe.
                # On retourne l'erreur pour analyse.
                # Dans une future itération, on pourrait appeler un prompt de correction ici.
                return None, last_error

            except Exception as e:
                error_msg = f"Erreur inattendue lors de la validation: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                return None, error_msg

        self.logger.error(f"Échec de la conversion après {max_retries} tentatives. Dernière erreur: {last_error}")
        return None, f"Échec de la conversion après {max_retries} tentatives. Dernière erreur: {last_error}"

    def _extract_json_block(self, text: str) -> str:
        """Extrait le premier bloc JSON valide de la réponse du LLM avec gestion des troncatures."""
        start_index = text.find('{')
        if start_index == -1:
            self.logger.warning("Aucun début de JSON trouvé.")
            return text
        
        # Tentative d'extraction du JSON complet
        end_index = text.rfind('}')
        if start_index != -1 and end_index != -1 and end_index > start_index:
            potential_json = text[start_index:end_index + 1]
            
            # Test si le JSON est valide
            try:
                json.loads(potential_json)
                return potential_json
            except json.JSONDecodeError:
                self.logger.warning("JSON potentiellement tronqué détecté. Tentative de réparation...")
                
        # Tentative de réparation pour JSON tronqué
        partial_json = text[start_index:]
        
        # Compter les accolades ouvertes non fermées
        open_braces = 0
        valid_end = len(partial_json)
        
        for i, char in enumerate(partial_json):
            if char == '{':
                open_braces += 1
            elif char == '}':
                open_braces -= 1
                if open_braces == 0:
                    valid_end = i + 1
                    break
        
        # Si on a des accolades non fermées, essayer de fermer proprement
        if open_braces > 0:
            self.logger.warning(f"JSON tronqué détecté ({open_braces} accolades non fermées). Tentative de complétion...")
            repaired_json = partial_json[:valid_end] + '}' * open_braces
            
            try:
                json.loads(repaired_json)
                self.logger.info("Réparation JSON réussie.")
                return repaired_json
            except json.JSONDecodeError:
                self.logger.error("Échec de la réparation JSON.")
        
        self.logger.warning("Retour du JSON partiel original.")
        return partial_json[:valid_end] if valid_end < len(partial_json) else partial_json

    def _normalize_and_validate_json(self, kb_json: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise les identifiants et valide la cohérence interne du JSON."""
        normalized_kb_json = {"predicates": kb_json.get("predicates", [])}
        constant_map = {}
        normalized_sorts = {}
        for sort_name, constants in kb_json.get("sorts", {}).items():
            norm_constants = []
            for const in constants:
                norm_const = self._normalize_identifier(const)
                norm_constants.append(norm_const)
                constant_map[const] = norm_const
            normalized_sorts[sort_name] = norm_constants
        normalized_kb_json["sorts"] = normalized_sorts

        normalized_formulas = []
        for formula in kb_json.get("formulas", []):
            sorted_constants = sorted(constant_map.keys(), key=len, reverse=True)
            norm_formula = formula
            for const in sorted_constants:
                norm_formula = re.sub(r'\b' + re.escape(const) + r'\b', constant_map[const], norm_formula)
            normalized_formulas.append(norm_formula)
        normalized_kb_json["formulas"] = normalized_formulas
        
        is_json_valid, json_validation_msg = self._validate_kb_json(normalized_kb_json)
        if not is_json_valid:
            raise ValueError(json_validation_msg)
        
        self.logger.info("Validation de la cohérence du JSON réussie.")
        return normalized_kb_json
    

    def _parse_belief_set_content(self, belief_set_content: str) -> Dict[str, Any]:
        """
        Analyse le contenu textuel d'un belief set pour en extraire les sorts,
        constantes et prédicats avec leur arité.
        """
        knowledge_base = {
            "sorts": {},
            "constants": set(),
            "predicates": {}
        }

        # Extraction des sorts et constantes
        sort_matches = re.findall(r'(\w+)\s*=\s*\{\s*([^}]+)\s*\}', belief_set_content)
        for sort_name, constants_str in sort_matches:
            constants = {c.strip() for c in constants_str.split(',')}
            knowledge_base["sorts"][sort_name] = constants
            knowledge_base["constants"].update(constants)

        # Extraction des prédicats et de leur arité
        predicate_matches = re.findall(r'type\(([\w_]+)(?:\(([^)]*)\))?\)', belief_set_content)
        for pred_name, args_str in predicate_matches:
            if args_str:
                args = [arg.strip() for arg in args_str.split(',')]
                arity = len(args)
            else:
                arity = 0
            knowledge_base["predicates"][pred_name] = arity
            
        return knowledge_base

    async def generate_queries(self, text: str, belief_set: BeliefSet, context: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        Génère des requêtes FOL valides en utilisant une approche de "Modèle de Requête".
        
        1. Le LLM génère des "idées" de requêtes (prédicat + constantes).
        2. Le code Python valide rigoureusement chaque idée par rapport à la base de connaissances.
        3. Le code Python assemble les requêtes finales uniquement pour les idées valides.
        
        Cette approche garantit que 100% des requêtes générées sont syntaxiquement et
        sémantiquement correctes.
        """
        self.logger.info(f"Génération de requêtes FOL via le modèle de requête pour {self.name}...")
        response_text = ""
        try:
            # Étape 1: Extraire les informations de la base de connaissances
            kb_details = self._parse_belief_set_content(belief_set.content)
            self.logger.debug(f"Détails de la KB extraits: {kb_details}")

            # Étape 2: Générer les idées de requêtes avec le LLM
            args = {
                "input": text,
                "belief_set": belief_set.content
            }
            
            result = await self.sk_kernel.plugins[self.name]["GenerateFOLQueryIdeas"].invoke(self.sk_kernel, **args)
            response_text = str(result)
            
            # Extraire le bloc JSON de la réponse
            json_block = self._extract_json_block(response_text)
            if not json_block:
                self.logger.error("Aucun bloc JSON trouvé dans la réponse du LLM pour les idées de requêtes.")
                return []
                
            query_ideas_data = json.loads(json_block)
            query_ideas = query_ideas_data.get("query_ideas", [])

            if not query_ideas:
                self.logger.warning("Le LLM n'a généré aucune idée de requête.")
                return []

            self.logger.info(f"{len(query_ideas)} idées de requêtes reçues du LLM.")
            self.logger.debug(f"Idées de requêtes brutes reçues: {json.dumps(query_ideas, indent=2)}")

            # Étape 3: Assemblage et validation des requêtes
            valid_queries = []
            for idea in query_ideas:
                predicate_name = idea.get("predicate_name")
                constants = idea.get("constants", [])

                # Validation 1: Le nom du prédicat est-il une chaîne de caractères ?
                if not isinstance(predicate_name, str):
                    self.logger.info(f"Idée de requête rejetée: 'predicate_name' invalide (pas une chaîne) -> {predicate_name}")
                    continue

                # Validation 2: Le prédicat existe-t-il dans la KB ?
                if predicate_name not in kb_details["predicates"]:
                    self.logger.info(f"Idée de requête rejetée: Prédicat inconnu '{predicate_name}'.")
                    continue

                # Validation 3: Les constantes sont-elles une liste ?
                if not isinstance(constants, list):
                    self.logger.info(f"Idée de requête rejetée pour '{predicate_name}': 'constants' n'est pas une liste -> {constants}")
                    continue

                # Validation 4: Toutes les constantes existent-elles dans la KB ?
                invalid_consts = [c for c in constants if c not in kb_details["constants"]]
                if invalid_consts:
                    self.logger.info(f"Idée de requête rejetée pour '{predicate_name}': Constantes inconnues: {invalid_consts}")
                    continue

                # Validation 5: L'arité du prédicat correspond-elle au nombre de constantes ?
                expected_arity = kb_details["predicates"][predicate_name]
                actual_arity = len(constants)
                if expected_arity != actual_arity:
                    self.logger.info(f"Idée de requête rejetée pour '{predicate_name}': Arity incorrecte. Attendu: {expected_arity}, Reçu: {actual_arity}")
                    continue
                
                # Si toutes les validations passent, on assemble la requête
                query_string = f"{predicate_name}({', '.join(constants)})"
                
                # Validation contextuelle avec Tweety
                validation_result = self.tweety_bridge.validate_fol_query_with_context(belief_set.content, query_string)
                is_valid, validation_msg = validation_result if isinstance(validation_result, tuple) else (validation_result, "")
                if is_valid:
                    self.logger.info(f"Idée validée et requête assemblée: {query_string}")
                    valid_queries.append(query_string)
                else:
                    self.logger.info(f"Idée rejetée: La requête assemblée '{query_string}' a échoué la validation de Tweety: {validation_msg}")

            self.logger.info(f"Génération terminée. {len(valid_queries)}/{len(query_ideas)} requêtes valides assemblées.")
            return valid_queries

        except json.JSONDecodeError as e:
            self.logger.error(f"Erreur de décodage JSON lors de la génération des requêtes: {e}\nRéponse du LLM: {response_text}")
            return []
        except Exception as e:
            self.logger.error(f"Erreur inattendue lors de la génération des requêtes: {e}", exc_info=True)
            return []
    
    def execute_query(self, belief_set: BeliefSet, query: str) -> Tuple[Optional[bool], str]:
        """
        Exécute une requête logique du premier ordre (FOL) sur un ensemble de croyances donné.

        Utilise `TweetyBridge` pour exécuter la requête contre le contenu de `belief_set`.
        Interprète la chaîne de résultat de `TweetyBridge` pour déterminer si la requête
        est acceptée, rejetée ou si une erreur s'est produite.

        :param belief_set: L'ensemble de croyances FOL sur lequel exécuter la requête.
        :type belief_set: BeliefSet
        :param query: La requête FOL à exécuter.
        :type query: str
        :return: Un tuple contenant le résultat booléen de la requête (`True` si acceptée,
                 `False` si rejetée, `None` si indéterminé ou erreur) et la chaîne de
                 résultat brute de `TweetyBridge` (ou un message d'erreur).
        :rtype: Tuple[Optional[bool], str]
        """
        self.logger.info(f"Exécution de la requête: {query} pour l'agent {self.name}")
        
        try:
            bs_str = belief_set.content # Utiliser .content
            
            result_str = self.tweety_bridge.execute_fol_query(
                belief_set_content=bs_str,
                query_string=query
            )
            
            if result_str is None or "ERROR" in result_str.upper(): 
                self.logger.error(f"Erreur lors de l'exécution de la requête: {result_str}")
                return None, result_str if result_str else "Erreur inconnue de TweetyBridge"
            
            if "ACCEPTED" in result_str: 
                return True, result_str
            elif "REJECTED" in result_str:
                return False, result_str
            else:
                self.logger.warning(f"Résultat de requête inattendu: {result_str}")
                return None, result_str
        
        except Exception as e:
            error_msg = f"Erreur lors de l'exécution de la requête: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return None, f"FUNC_ERROR: {error_msg}" 

    async def interpret_results(self, text: str, belief_set: BeliefSet,
                         queries: List[str], results: List[Tuple[Optional[bool], str]],
                         context: Optional[Dict[str, Any]] = None) -> str:
        """
        Interprète les résultats d'une série de requêtes FOL en langage naturel.

        Utilise la fonction sémantique "InterpretFOLResult" pour générer une explication
        basée sur le texte original, l'ensemble de croyances, les requêtes posées et
        les résultats obtenus de Tweety.

        :param text: Le texte original en langage naturel.
        :type text: str
        :param belief_set: L'ensemble de croyances FOL utilisé.
        :type belief_set: BeliefSet
        :param queries: La liste des requêtes FOL qui ont été exécutées.
        :type queries: List[str]
        :param results: La liste des résultats (tuples booléen/None, message_brut)
                        correspondant à chaque requête.
        :type results: List[Tuple[Optional[bool], str]]
        :param context: Un dictionnaire optionnel de contexte (non utilisé actuellement).
        :type context: Optional[Dict[str, Any]]
        :return: Une chaîne de caractères contenant l'interprétation en langage naturel
                 des résultats, ou un message d'erreur.
        :rtype: str
        """
        self.logger.info(f"Interprétation des résultats pour l'agent {self.name}...")
        
        try:
            queries_str = "\n".join(queries)
            results_text_list = [res_tuple[1] if res_tuple else "Error: No result" for res_tuple in results]
            results_str = "\n".join(results_text_list)
            
            result = await self.sk_kernel.plugins[self.name]["InterpretFOLResult"].invoke(
                self.sk_kernel,
                input=text,
                belief_set=belief_set.content, # Utiliser .content
                queries=queries_str,
                tweety_result=results_str
            )
            
            interpretation = str(result)
            self.logger.info("Interprétation terminée")
            return interpretation
        
        except Exception as e:
            error_msg = f"Erreur lors de l'interprétation des résultats: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return f"Erreur d'interprétation: {error_msg}"

    def validate_formula(self, formula: str) -> bool:
        """
        Valide la syntaxe d'une formule de logique du premier ordre (FOL).

        Utilise la méthode `validate_fol_formula` de `TweetyBridge`.

        :param formula: La formule FOL à valider.
        :type formula: str
        :return: `True` si la formule est syntaxiquement valide, `False` sinon.
        :rtype: bool
        """
        self.logger.debug(f"Validation de la formule FOL: {formula}")
        is_valid, message = self.tweety_bridge.validate_fol_formula(formula)
        if not is_valid:
            self.logger.warning(f"Formule FOL invalide: {formula}. Message: {message}")
        return is_valid

    def is_consistent(self, belief_set: BeliefSet) -> Tuple[bool, str]:
        """
        Vérifie si un ensemble de croyances FOL est cohérent.

        :param belief_set: L'ensemble de croyances à vérifier.
        :return: Un tuple (bool, str) indiquant la cohérence et un message.
        """
        self.logger.info(f"Vérification de la cohérence pour l'agent {self.name}")
        try:
            # La signature est extraite du belief_set.content par le handler
            is_consistent, message = self.tweety_bridge.is_fol_kb_consistent(belief_set.content)
            if not is_consistent:
                self.logger.warning(f"Ensemble de croyances FOL jugé incohérent par Tweety: {message}")
            return is_consistent, message
        except Exception as e:
            error_msg = f"Erreur inattendue lors de la vérification de la cohérence FOL: {e}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg

    def _create_belief_set_from_data(self, belief_set_data: Dict[str, Any]) -> BeliefSet:
        """
        Crée un objet `FirstOrderBeliefSet` à partir d'un dictionnaire de données.

        Principalement utilisé pour reconstituer un `BeliefSet` à partir d'un état sauvegardé.

        :param belief_set_data: Un dictionnaire contenant au moins la clé "content"
                                avec la représentation textuelle de l'ensemble de croyances.
        :type belief_set_data: Dict[str, Any]
        :return: Une instance de `FirstOrderBeliefSet`.
        :rtype: BeliefSet
        """
        content = belief_set_data.get("content", "")
        return FirstOrderBeliefSet(content)

    async def get_response(
        self,
        chat_history: ChatHistory,
        settings: Optional[Any] = None, # Remplace PromptExecutionSettings
    ) -> AsyncGenerator[list[ChatMessageContent], None]:
        """
        Méthode abstraite de `Agent` pour obtenir une réponse.
        Non implémentée car cet agent utilise des méthodes spécifiques.
        """
        logger.warning("La méthode 'get_response' n'est pas implémentée pour FirstOrderLogicAgent et ne devrait pas être appelée directement.")
        yield []
        return

    async def invoke(
        self,
        chat_history: ChatHistory,
        settings: Optional[Any] = None, # Remplace PromptExecutionSettings
    ) -> list[ChatMessageContent]:
        """
        Méthode abstraite de `Agent` pour invoquer l'agent.
        Non implémentée car cet agent utilise des méthodes spécifiques.
        """
        logger.warning("La méthode 'invoke' n'est pas implémentée pour FirstOrderLogicAgent et ne devrait pas être appelée directement.")
        return []

    async def invoke_stream(
        self,
        chat_history: ChatHistory,
        settings: Optional[Any] = None, # Remplace PromptExecutionSettings
    ) -> AsyncGenerator[list[ChatMessageContent], None]:
        """
        Méthode abstraite de `Agent` pour invoquer l'agent en streaming.
        Non implémentée car cet agent utilise des méthodes spécifiques.
        """
        logger.warning("La méthode 'invoke_stream' n'est pas implémentée pour FirstOrderLogicAgent et ne devrait pas être appelée directement.")
        yield []
        return