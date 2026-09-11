"use client";

import { useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";
import LanguageSwitcher from "./LanguageSwitcher";
import FontToggle from "./FontToggle";
import FontSize from "./FontSize";
import ThemeSelector from "./ThemeSelector";
import styles from "./FloatingTools.module.css";

export default function FloatingTools() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className={`${styles.container} ${isOpen ? styles.open : ""}`}>
      {isOpen && (
        <div className={styles.menu}>
          <div className={styles.item}><ThemeSelector /></div>
          <div className={styles.item}><FontToggle /></div>
          <div className={styles.item}><FontSize /></div>
          <div className={styles.item}><LanguageSwitcher /></div>
        </div>
      )}
      
      <button 
        className={styles.trigger} 
        onClick={() => setIsOpen(!isOpen)}
        title={isOpen ? "Fermer les paramètres" : "Paramètres d'accessibilité"}
      >
        {isOpen ? <ChevronDown size={20} /> : <ChevronUp size={20} />}
      </button>
    </div>
  );
}
